from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.security import SecurityCog


@pytest.mark.asyncio
async def test_alert_cooldown_does_not_skip_deleting_followup_scams():
    cog = object.__new__(SecurityCog)
    cog.scam_alerts = {}
    cog._enabled = lambda _: True
    cog._has_scam_bypass_role = lambda _: False
    cog._int_setting = lambda *_: 60
    cog._bool_setting = lambda *_: True
    cog.cases = SimpleNamespace(create_case=MagicMock())
    cog._alert = AsyncMock()
    messages = [
        SimpleNamespace(
            guild=SimpleNamespace(id=1),
            author=SimpleNamespace(id=2, bot=False, mention="@user"),
            channel=SimpleNamespace(mention="#channel"),
            id=message_id,
            content="Free Nitro: https://disc0rd-gift.example",
            jump_url="https://discord.com/channels/1/3/4",
            delete=AsyncMock(),
        )
        for message_id in (4, 5)
    ]
    for message in messages:
        await cog.on_message(message)

    for message in messages:
        message.delete.assert_awaited_once()
    cog._alert.assert_awaited_once()
    cog.cases.create_case.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [-1, 0, 86401])
async def test_invalid_lock_does_not_change_channel_or_existing_timer(seconds):
    cog = object.__new__(SecurityCog)
    existing_timer = MagicMock()
    cog.channel_previous = {(1, 2): True}
    cog.lock_tasks = {(1, 2): existing_timer}
    cog._set_channel_lock = AsyncMock()
    channel = SimpleNamespace(
        id=2,
        guild=SimpleNamespace(id=1, default_role=object()),
        overwrites_for=MagicMock(),
    )
    interaction = SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock())
    )

    await SecurityCog.lock.callback(cog, interaction, channel, seconds)

    cog._set_channel_lock.assert_not_awaited()
    channel.overwrites_for.assert_not_called()
    existing_timer.cancel.assert_not_called()
    assert cog.channel_previous == {(1, 2): True}
    assert cog.lock_tasks == {(1, 2): existing_timer}
    interaction.response.send_message.assert_awaited_once_with(
        "The temporary lock must be 1 to 86400 seconds.", ephemeral=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "https://bit.ly/example",
    "Download https://example.com/source.zip",
    "Login https://example.com/oauth/authorize",
])
async def test_weak_signals_alert_without_automatic_deletion(content):
    cog = object.__new__(SecurityCog)
    cog.scam_alerts = {}
    cog._enabled = lambda _: True
    cog._has_scam_bypass_role = lambda _: False
    cog._int_setting = lambda *_: 60
    cog._bool_setting = lambda *_: True
    cog.cases = SimpleNamespace(create_case=MagicMock())
    cog._alert = AsyncMock()
    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=2, bot=False, mention="@user"),
        channel=SimpleNamespace(mention="#channel"),
        id=4, content=content, jump_url="https://discord.com/channels/1/3/4",
        delete=AsyncMock(),
    )
    await cog.on_message(message)
    message.delete.assert_not_awaited()
    cog._alert.assert_awaited_once()
