import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import discord
import pytest

from cogs.steam_free import (
    History,
    Offer,
    SteamClient,
    SteamFree,
    SteamResponseError,
    make_post,
    parse_offer,
    setup,
)


def page(**changes):
    option = {
        "is_free_to_keep": 1,
        "discount_pct": 100,
        "final_price_in_cents": "0",
        "original_price_in_cents": "999",
        "formatted_original_price": "€9.99",
        "free_to_keep_ends": 2000,
    }
    option.update(changes)
    return json.dumps(
        {
            "rgStoreItems": [
                {"appid": 42, "type": 0, "name": "Game", "best_purchase_option": option}
            ]
        }
    )


def test_accepts_verified_promotion():
    assert parse_offer(42, page(), "si", now=1000).original == "€9.99"


@pytest.mark.parametrize(
    "changes",
    [
        {"is_free_to_keep": 0},
        {"discount_pct": 90},
        {"final_price_in_cents": "99"},
        {"original_price_in_cents": "0"},
        {"free_to_keep_ends": 999},
        {"free_to_keep_ends": None},
        {"final_price_in_cents": "nan"},
    ],
)
def test_rejects_unverified_or_expired(changes):
    assert parse_offer(42, page(**changes), "si", now=1000) is None


def test_wrong_app_and_markup():
    assert parse_offer(99, page(), "si", now=1000) is None
    assert parse_offer(42, "Free to play", "si", now=1000) is None


def test_dlc_requires_parent():
    data = json.loads(page())
    data["rgStoreItems"][0]["type"] = 4
    assert parse_offer(42, json.dumps(data), "si", now=1000) is None
    data["rgStoreItems"][0]["related_items"] = {"parent_appid": 1}
    assert parse_offer(42, json.dumps(data), "si", now=1000).base_id == 1


def test_history_survives_restart_and_allows_new_promotion(tmp_path):
    path = tmp_path / "test.db"
    offer = Offer(42, "Game", "€9.99", 2000, "si")
    history = History(path)
    assert not history.seen(1, offer)
    history.record(1, offer, 5)
    assert History(path).seen(1, offer)
    assert not history.seen(2, offer)
    assert not history.seen(1, Offer(42, "Game", "€9.99", 3000, "si"))


@pytest.mark.asyncio
async def test_disabled_setup_has_no_side_effects(monkeypatch):
    monkeypatch.setattr("cogs.steam_free.RELEASED", False)
    bot = AsyncMock()
    with pytest.raises(RuntimeError, match="disabled"):
        await setup(bot)
    bot.add_cog.assert_not_called()


@pytest.mark.asyncio
async def test_released_setup_registers_cog():
    bot = AsyncMock()
    await setup(bot)
    assert isinstance(bot.add_cog.call_args.args[0], SteamFree)


@pytest.mark.asyncio
async def test_enabled_cog_starts_and_stops_polling(tmp_path, monkeypatch):
    import cogs.steam_free as module

    monkeypatch.setattr(module, "DATABASE_PATH", tmp_path / "bot.db")
    bot = SimpleNamespace(wait_until_ready=asyncio.Event().wait)
    cog = SteamFree(bot)
    await cog.cog_load()
    try:
        assert cog.poll.is_running()
        assert not cog.session.closed
    finally:
        await cog.cog_unload()
    assert cog.session.closed
    task = cog.poll.get_task()
    if task:
        await asyncio.gather(task, return_exceptions=True)


def test_steam_extension_and_setup_settings_are_available():
    import ast
    from pathlib import Path

    from cogs.setup_ui import get_module

    module = get_module("steam_free")
    assert {s.key for s in module.settings} == {"enabled", "channel", "country"}
    assert next(s for s in module.settings if s.key == "country").default == "si"
    tree = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text())
    bot_class = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MyBot"
    )
    extensions = next(
        n
        for n in bot_class.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "EXTENSIONS" for t in n.targets)
    )
    assert "cogs.steam_free" in ast.literal_eval(extensions.value)


@pytest.mark.asyncio
async def test_embed_has_base_game_and_cyan():
    image_url = "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/42/hash/header.jpg"
    embed, view = make_post(Offer(42, "DLC", "€9.99", 2000, "si", 1, "Base", image_url))
    assert embed.color.value == 0x96EDF1
    assert "base game required" in embed.fields[-1].name
    assert "/app/1/" in embed.fields[-1].value
    assert view.children[0].url.startswith("https://store.steampowered.com/")
    assert embed.image.url == image_url
    assert not embed.thumbnail.url


@pytest.mark.parametrize("field", ["best_purchase_option", "related_items"])
def test_malformed_item_fields_are_skipped(field):
    data = json.loads(page())
    data["rgStoreItems"][0][field] = "unexpected"
    assert parse_offer(42, json.dumps(data), "si", now=1000) is None


def response(status=200, text="ok", headers=None):
    result = AsyncMock()
    result.status = status
    result.headers = headers or {}
    result.text.return_value = text
    result.raise_for_status = Mock()
    result.__aenter__.return_value = result
    return result


@pytest.mark.asyncio
async def test_transient_steam_failure_retries(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("cogs.steam_free.asyncio.sleep", sleep)
    session = Mock()
    session.get.side_effect = [response(503), response(text="recovered")]
    assert await SteamClient(session).get("https://example.invalid") == "recovered"
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), aiohttp.ClientConnectionError()])
async def test_network_retries_are_bounded(monkeypatch, failure):
    monkeypatch.setattr("cogs.steam_free.asyncio.sleep", AsyncMock())
    session = Mock()
    session.get.side_effect = failure
    with pytest.raises(SteamResponseError, match="three attempts"):
        await SteamClient(session).get("https://example.invalid")
    assert session.get.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", ["120", "invalid", "nan"])
async def test_rate_limit_long_or_unknown_deadline_does_not_hammer(
    monkeypatch, deadline
):
    sleep = AsyncMock()
    monkeypatch.setattr("cogs.steam_free.asyncio.sleep", sleep)
    session = Mock()
    session.get.return_value = response(429, headers={"Retry-After": deadline})
    with pytest.raises(SteamResponseError):
        await SteamClient(session).get("https://example.invalid")
    assert session.get.call_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_is_propagated():
    session = Mock()
    session.get.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await SteamClient(session).get("https://example.invalid")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", ["not json", "[]", '{"results_html": [], "total_count": 2}']
)
async def test_bad_search_data_is_reported(body):
    client = SteamClient(Mock())
    client.get = AsyncMock(return_value=body)
    with pytest.raises(SteamResponseError, match="invalid data"):
        await client.candidates("si")


@pytest.mark.asyncio
async def test_parent_name_failure_keeps_verified_dlc_link():
    data = json.loads(page(free_to_keep_ends=int(time.time()) + 3600))
    data["rgStoreItems"][0].update(type=4, related_items={"parent_appid": 1})
    client = SteamClient(Mock())
    client.get = AsyncMock(
        side_effect=[json.dumps(data), SteamResponseError("unavailable")]
    )
    offer = await client.offer(42, "si")
    assert offer.base_id == 1 and offer.base_name is None


@pytest.mark.asyncio
async def test_db_failure_after_send_does_not_repeat_message():
    cog = SteamFree(Mock())
    cog.history = Mock()
    cog.history.seen.return_value = False
    cog.history.record.side_effect = [sqlite3.OperationalError("locked"), None]
    channel = SimpleNamespace(id=7, send=AsyncMock(return_value=SimpleNamespace(id=8)))
    offer = Offer(42, "Game", "€9.99", int(time.time()) + 3600, "si")
    await cog.deliver(1, channel, offer)
    await cog.deliver(1, channel, offer)
    channel.send.assert_awaited_once()
    assert not cog._pending_history


@pytest.mark.asyncio
async def test_failed_discord_send_is_not_recorded_or_retried():
    cog = SteamFree(Mock())
    cog.history = Mock()
    cog.history.seen.return_value = False
    channel = SimpleNamespace(id=7, send=AsyncMock(side_effect=RuntimeError("failed")))
    offer = Offer(42, "Game", "€9.99", int(time.time()) + 3600, "si")
    with pytest.raises(RuntimeError):
        await cog.deliver(1, channel, offer)
    channel.send.assert_awaited_once()
    cog.history.record.assert_not_called()
    assert not cog._pending_history


@pytest.mark.asyncio
async def test_short_rate_limit_respects_retry_after(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("cogs.steam_free.asyncio.sleep", sleep)
    session = Mock()
    session.get.side_effect = [response(429, headers={"Retry-After": "3"}), response()]
    assert await SteamClient(session).get("https://example.invalid") == "ok"
    sleep.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_permanent_http_error_is_not_retried():
    session = Mock()
    reply = response(403)
    reply.raise_for_status.side_effect = aiohttp.ClientResponseError(
        Mock(), (), status=403
    )
    session.get.return_value = reply
    with pytest.raises(SteamResponseError, match="403"):
        await SteamClient(session).get("https://example.invalid")
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_expired_offer_is_not_sent():
    cog = SteamFree(Mock())
    cog.history = Mock()
    channel = SimpleNamespace(id=7, send=AsyncMock())
    await cog.deliver(1, channel, Offer(42, "Game", "€9.99", 1, "si"))
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_history_retried_even_after_offer_expires():
    cog = SteamFree(SimpleNamespace(guilds=[]))
    cog.history = Mock()
    cog.history.pending_end_checks.return_value = []
    cog._pending_history[(1, 42, 1)] = 8
    await cog.scan()
    cog.history.record.assert_called_once()
    assert cog.history.record.call_args.args[2] == 8
    assert not cog._pending_history


def test_image_uses_actual_steam_header_with_social_fallback():
    header = "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/42/hash/header.jpg"
    social = "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/42/hash/capsule.jpg"
    markup = f'<meta property="og:image" content="{social}">'
    assert parse_offer(42, page() + markup, "si", now=1000).image_url == social
    markup += f'<img class="game_header_image_full" src="{header}">'
    assert parse_offer(42, page() + markup, "si", now=1000).image_url == header


@pytest.mark.parametrize(
    "url", ["https://evil.invalid/apps/42/header.jpg", "javascript:alert(1)"]
)
def test_untrusted_image_urls_are_ignored(url):
    markup = f'<img class="game_header_image_full" src="{url}">'
    assert parse_offer(42, page() + markup, "si", now=1000).image_url is None


def test_history_migration_preserves_existing_announcements(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE steam_free_posts (guild_id INTEGER, app_id INTEGER, "
            "ends INTEGER, message_id INTEGER, "
            "PRIMARY KEY(guild_id, app_id, ends))"
        )
        db.execute("INSERT INTO steam_free_posts VALUES (1, 42, 2000, 777)")

    store = History(path)
    rows = store.pending_end_checks()
    assert len(rows) == 1
    assert rows[0]["message_id"] == 777
    assert rows[0]["channel_id"] is None
    assert rows[0]["country"] is None
    assert History(path).seen(1, Offer(42, "Game", "€9.99", 2000, "de"))
    store.mark_ended(1, 42, 2000)
    assert History(path).pending_end_checks() == []
    assert store.seen(1, Offer(42, "Game", "€9.99", 2000, "de"))


def test_history_records_channel_and_region_across_restarts(tmp_path):
    path = tmp_path / "posts.db"
    offer = Offer(42, "Game", "€9.99", 2000, "de")
    History(path).record(1, offer, 123456789, channel_id=77)
    posts = History(path).pending_end_checks()
    assert len(posts) == 1
    assert posts[0]["channel_id"] == 77
    assert posts[0]["country"] == "de"
    assert posts[0]["message_id"] == 123456789


@pytest.mark.asyncio
async def test_restored_price_requires_confirmed_paid_price():
    client = SteamClient(Mock())
    client.get = AsyncMock(return_value=page(
        is_free_to_keep=0,
        discount_pct=0,
        final_price_in_cents="999",
        formatted_final_price="€9.99",
        free_to_keep_ends=0,
    ))
    assert await client.restored_price(42, "de") == "€9.99"

    # An active promotion must never be marked ended.
    client.get.return_value = page(free_to_keep_ends=int(time.time()) + 3600)
    assert await client.restored_price(42, "de") is None

    # Unknown prices must not produce a false expired announcement.
    client.get.return_value = page(best_purchase_option="unexpected")
    assert await client.restored_price(42, "de") is None

    client.get.return_value = "unexpected markup"
    with pytest.raises(SteamResponseError, match="cannot verify"):
        await client.restored_price(42, "de")


@pytest.mark.asyncio
async def test_ended_embed_retains_art_and_no_longer_says_free():
    from cogs.steam_free import make_ended_post

    offer = Offer(
        42, "Game", "€9.99", 2000, "de",
        image_url="https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/42/hash/header.jpg",
    )
    original, _ = make_post(offer)
    ended = make_ended_post(original, offer.ends, "€9.99")
    assert ended.title.startswith("ENDED • ")
    assert "ended" in ended.description.lower()
    assert ended.image.url == original.image.url
    assert ended.url == original.url
    assert "€9.99" in next(f.value for f in ended.fields if f.name == "Current price")
    assert not any("100% off" in f.value for f in ended.fields)


@pytest.mark.asyncio
async def test_original_message_edited_once_after_price_returns(tmp_path, monkeypatch):
    import cogs.steam_free as module

    original, _ = make_post(Offer(42, "Game", "€9.99", 2000, "de"))
    message = SimpleNamespace(
        author=SimpleNamespace(id=900),
        embeds=[original],
        edit=AsyncMock(),
    )

    class FakeChannel:
        id = 77

        def permissions_for(self, member):
            return SimpleNamespace(
                view_channel=True, read_message_history=True, embed_links=True
            )

        async def fetch_message(self, message_id):
            assert message_id == 123456789
            return message

    monkeypatch.setattr(module.discord, "TextChannel", FakeChannel)
    guild = Mock()
    guild.me = SimpleNamespace(id=900)
    guild.get_channel.return_value = FakeChannel()
    bot = Mock()
    bot.user = SimpleNamespace(id=900)
    bot.get_guild.return_value = guild
    # Even if the server disables new posts or changes settings, the original
    # channel and region saved with the message remain authoritative.
    bot.setup_store.get_module.return_value = {
        "enabled": False, "channel": 44, "country": "si"
    }
    path = tmp_path / "posted.db"
    history = History(path)
    history.record(1, Offer(42, "Game", "€9.99", 2000, "de"), 123456789, 77)

    cog = SteamFree(bot)
    cog.history = history
    client = Mock()
    client.restored_price = AsyncMock(return_value="€9.99")
    await cog.update_ended_posts(client)

    message.edit.assert_awaited_once()
    edited = message.edit.call_args.kwargs
    assert edited["view"] is None
    assert edited["embed"].title.startswith("ENDED • ")
    assert "€9.99" in next(
        f.value for f in edited["embed"].fields if f.name == "Current price"
    )
    client.restored_price.assert_awaited_once_with(42, "de")
    assert History(path).pending_end_checks() == []

    # A later scan, or a new bot process, should not edit the same post again.
    cog.history = History(path)
    await cog.update_ended_posts(client)
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_message_edit_when_steam_price_is_unknown(tmp_path, monkeypatch):
    import cogs.steam_free as module

    class FakeChannel:
        id = 77
        def permissions_for(self, member):
            return SimpleNamespace(
                view_channel=True, read_message_history=True, embed_links=True
            )
        fetch_message = AsyncMock()

    monkeypatch.setattr(module.discord, "TextChannel", FakeChannel)
    guild = Mock()
    guild.me = SimpleNamespace(id=900)
    guild.get_channel.return_value = FakeChannel()
    bot = Mock()
    bot.user = SimpleNamespace(id=900)
    bot.get_guild.return_value = guild
    bot.setup_store.get_module.return_value = {"country": "de"}
    path = tmp_path / "posted.db"
    history = History(path)
    history.record(1, Offer(42, "Game", "€9.99", 2000, "de"), 555, 77)
    cog = SteamFree(bot)
    cog.history = history
    client = Mock()
    client.restored_price = AsyncMock(return_value=None)
    await cog.update_ended_posts(client)
    guild.get_channel.return_value.fetch_message.assert_not_awaited()
    assert len(History(path).pending_end_checks()) == 1
