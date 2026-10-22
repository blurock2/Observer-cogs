"""Unreleased Steam alerts. Deliberately absent from bot.EXTENSIONS and /setup."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

import aiohttp
import discord
from discord.ext import commands, tasks

from database import DATABASE_PATH, connect_sqlite
from error_handling import log_error

logger = logging.getLogger("observer.steam_free")
RELEASED = False
STORE = "https://store.steampowered.com"


class SteamResponseError(RuntimeError):
    """Steam is unavailable or returned an unexpected response; skip this scan."""


class StoreImageParser(HTMLParser):
    def __init__(self, app_id):
        super().__init__()
        self.app_id = app_id
        self.header = None
        self.social = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        header = (
            tag == "img"
            and "game_header_image_full" in (attrs.get("class") or "").split()
        )
        social = tag == "meta" and attrs.get("property") == "og:image"
        value = attrs.get("src" if header else "content") if header or social else None
        if not value:
            return
        try:
            url = urlsplit(value)
            valid = (
                url.scheme == "https"
                and url.hostname is not None
                and url.hostname.endswith(".steamstatic.com")
                and f"/apps/{self.app_id}/" in url.path
            )
        except ValueError:
            return
        if valid:
            if header:
                self.header = value
            else:
                self.social = value


def store_items(source: str) -> list[dict]:
    """Read Steam's embedded item data; unknown markup fails closed."""
    source = html.unescape(source)
    items = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r'"rgStoreItems"\s*:\s*', source):
        try:
            value, _ = decoder.raw_decode(source, match.end())
        except ValueError:
            continue
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


@dataclass(frozen=True)
class Offer:
    app_id: int
    name: str
    original: str
    ends: int
    country: str
    base_id: int | None = None
    base_name: str | None = None
    image_url: str | None = None

    @property
    def url(self):
        return f"{STORE}/app/{self.app_id}/?cc={self.country}&l=english"


def parse_offer(app_id: int, source: str, country: str, *, now=None) -> Offer | None:
    now = time.time() if now is None else now
    for item in store_items(source):
        if item.get("appid") != app_id or item.get("type") not in (0, 4):
            continue  # Steam store item types: game or DLC only.
        option = item.get("best_purchase_option") or {}
        related = item.get("related_items") or {}
        if not isinstance(option, dict) or not isinstance(related, dict):
            continue
        try:
            ends = int(option.get("free_to_keep_ends", 0))
            if (
                option.get("is_free_to_keep") != 1
                or int(option.get("discount_pct", 0)) != 100
                or int(option.get("final_price_in_cents", -1)) != 0
                or int(option.get("original_price_in_cents", 0)) <= 0
                or ends <= now
            ):
                continue
            parent = int(related.get("parent_appid") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if item.get("type") == 4 and parent <= 0:
            continue
        name = item.get("name")
        price = option.get("formatted_original_price")
        if not isinstance(name, str) or not name.strip() or not isinstance(price, str):
            continue
        images = StoreImageParser(app_id)
        images.feed(source)
        return Offer(
            app_id,
            name.strip(),
            price,
            ends,
            country,
            parent if item.get("type") == 4 else None,
            image_url=images.header or images.social,
        )
    return None


def make_post(offer: Offer):
    embed = discord.Embed(
        title=offer.name[:256],
        url=offer.url,
        color=0x96EDF1,
        description="Claim on Steam and keep it forever.",
    )
    embed.add_field(name="Price", value=f"~~{offer.original}~~ → **FREE · 100% off**")
    embed.add_field(
        name="Claim before", value=f"<t:{offer.ends}:F>\n<t:{offer.ends}:R>"
    )
    if offer.base_id:
        name = discord.utils.escape_markdown(
            offer.base_name or f"Steam app {offer.base_id}"
        )
        embed.add_field(
            name="DLC — base game required",
            value=f"[{name[:200]}]({STORE}/app/{offer.base_id}/)",
            inline=False,
        )
    # The full-width image is rendered below the fields, above Discord's footer.
    if offer.image_url:
        embed.set_image(url=offer.image_url)
    embed.set_footer(text=f"Observer • Steam offers • Region: {offer.country.upper()}")
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Claim on Steam", url=offer.url))
    return embed, view


class History:
    def __init__(self, path):
        self.path = path
        with closing(connect_sqlite(path)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS steam_free_posts ("
                "guild_id INTEGER, app_id INTEGER, ends INTEGER, message_id INTEGER, "
                "PRIMARY KEY(guild_id, app_id, ends))"
            )

    def seen(self, guild_id, offer):
        with closing(connect_sqlite(self.path)) as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM steam_free_posts WHERE guild_id=? AND app_id=? AND ends=?",
                    (guild_id, offer.app_id, offer.ends),
                ).fetchone()
                is not None
            )

    def record(self, guild_id, offer, message_id):
        with closing(connect_sqlite(self.path)) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO steam_free_posts VALUES (?, ?, ?, ?)",
                (guild_id, offer.app_id, offer.ends, message_id),
            )


class SteamClient:
    def __init__(self, session):
        self.session = session

    async def get(self, url, **params):
        for attempt in range(3):
            delay = 2**attempt
            try:
                async with self.session.get(url, params=params) as response:
                    if response.status == 429 or 500 <= response.status < 600:
                        retry_after = response.headers.get("Retry-After")
                        if retry_after is not None:
                            try:
                                delay = float(retry_after)
                            except ValueError:
                                # Don't retry sooner than an unrecognized server deadline.
                                raise SteamResponseError(
                                    "Steam requested an unknown retry deadline"
                                ) from None
                            if not 0 <= delay <= 30:
                                raise SteamResponseError(
                                    "Steam requested a long retry delay"
                                )
                        if attempt == 2:
                            raise SteamResponseError(
                                f"Steam HTTP {response.status}; retry limit reached"
                            )
                    else:
                        response.raise_for_status()
                        return await response.text()
            except aiohttp.ClientResponseError as error:
                raise SteamResponseError(f"Steam HTTP {error.status}") from error
            except (TimeoutError, aiohttp.ClientError) as error:
                if attempt == 2:
                    raise SteamResponseError(
                        "Steam request failed after three attempts"
                    ) from error
            logger.warning(
                "Steam request retry attempt=%s delay=%s", attempt + 1, delay
            )
            await asyncio.sleep(delay)

    async def candidates(self, country):
        ids = set()
        for start in range(0, 250, 50):
            source = await self.get(
                f"{STORE}/search/results/",
                start=start,
                count=50,
                maxprice="free",
                specials=1,
                cc=country,
                l="english",
                infinite=1,
            )
            try:
                data = json.loads(source)
                markup = data["results_html"]
                total = int(data["total_count"])
                if not isinstance(markup, str) or total < 0:
                    raise ValueError("Invalid Steam search response")
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                raise SteamResponseError(
                    "Steam search returned invalid data"
                ) from error
            ids.update(
                int(value) for value in re.findall(r'data-ds-appid="(\d+)"', markup)
            )
            if start + 50 >= total:
                break
            await asyncio.sleep(1)
        else:
            logger.warning("Steam discovery capped at 250 results country=%s", country)
        return sorted(ids)

    async def offer(self, app_id, country):
        source = await self.get(f"{STORE}/app/{app_id}/", cc=country, l="english")
        if not any(item.get("appid") == app_id for item in store_items(source)):
            raise SteamResponseError(
                "Steam item data missing; page may be unavailable or changed"
            )
        offer = parse_offer(app_id, source, country)
        if offer and not offer.image_url:
            logger.warning(
                "Steam image unavailable app=%s; posting text and claim link", app_id
            )
        if offer and offer.base_id:
            from dataclasses import replace

            try:
                parent = await self.get(
                    f"{STORE}/app/{offer.base_id}/", cc=country, l="english"
                )
            except SteamResponseError:
                logger.warning(
                    "Steam base game name unavailable app=%s base=%s",
                    app_id,
                    offer.base_id,
                )
                return offer  # Keep the verified base-game link and numeric label.
            name = next(
                (
                    x.get("name")
                    for x in store_items(parent)
                    if x.get("appid") == offer.base_id
                    and isinstance(x.get("name"), str)
                ),
                None,
            )
            offer = replace(offer, base_name=name)
        return offer


class SteamFree(commands.Cog):
    """No public commands; configuration UI will be added at release."""

    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self._pending_history = {}

    async def cog_load(self):
        if not RELEASED:
            raise RuntimeError("Steam alerts are unreleased and disabled.")
        self.history = await asyncio.to_thread(History, DATABASE_PATH)
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        try:
            self.poll.start()
        except Exception:
            await self.session.close()
            raise

    async def cog_unload(self):
        self.poll.cancel()
        if self.session:
            await self.session.close()

    @tasks.loop(minutes=30)
    async def poll(self):
        try:
            await self.scan()
        except Exception as error:  # noqa: BLE001 - keep the background scheduler alive and report errors
            log_error(error, source="steam_free.scan")

    @poll.before_loop
    async def ready(self):
        await self.bot.wait_until_ready()

    async def scan(self):
        for key, message_id in list(self._pending_history.items()):
            guild_id, app_id, ends = key
            try:
                await asyncio.to_thread(
                    self.history.record,
                    guild_id,
                    Offer(app_id, "", "", ends, "si"),
                    message_id,
                )
            except sqlite3.Error as error:
                log_error(
                    error,
                    source="steam_free.retry_save",
                    guild_id=guild_id,
                    reference=message_id,
                )
            else:
                del self._pending_history[key]
        client = SteamClient(self.session)
        cache = {}
        failed_countries = set()
        for guild in self.bot.guilds:
            try:
                config = await asyncio.to_thread(
                    self.bot.setup_store.get_module, guild.id, "steam_free"
                )
                if config.get("enabled") is not True:
                    continue
                country = str(config.get("country", "si")).lower()
                if not re.fullmatch(r"[a-z]{2}", country):
                    raise ValueError("Invalid Steam country code")
                channel = guild.get_channel(int(config.get("channel", 0)))
                if not isinstance(channel, discord.TextChannel):
                    logger.warning("Steam channel unavailable guild=%s", guild.id)
                    continue
                if guild.me is None:
                    logger.warning(
                        "Steam bot membership unavailable guild=%s", guild.id
                    )
                    continue
                permissions = channel.permissions_for(guild.me)
                if not (
                    permissions.view_channel
                    and permissions.send_messages
                    and permissions.embed_links
                ):
                    logger.warning(
                        "Steam posting permissions missing guild=%s channel=%s",
                        guild.id,
                        channel.id,
                    )
                    continue
                if country in failed_countries:
                    continue
                if country not in cache:
                    try:
                        cache[country] = await client.candidates(country)
                    except SteamResponseError:
                        failed_countries.add(country)
                        logger.warning(
                            "Steam discovery unavailable country=%s; skipping until next scan",
                            country,
                            exc_info=True,
                        )
                        continue
                for app_id in cache[country]:
                    try:
                        offer = await client.offer(app_id, country)
                        if not offer:
                            continue
                        await self.deliver(guild.id, channel, offer)
                    except SteamResponseError:
                        logger.warning(
                            "Steam offer unavailable guild=%s app=%s",
                            guild.id,
                            app_id,
                            exc_info=True,
                        )
                    except (discord.Forbidden, discord.NotFound):
                        logger.warning(
                            "Steam posting channel inaccessible guild=%s channel=%s",
                            guild.id,
                            channel.id,
                        )
                        break
                    except Exception as error:  # noqa: BLE001 - isolate and report one offer failure
                        log_error(
                            error,
                            source="steam_free.offer",
                            guild_id=guild.id,
                            channel_id=channel.id,
                            reference=app_id,
                        )
                    await asyncio.sleep(1)
            except Exception as error:  # noqa: BLE001 - isolate and report one guild failure
                log_error(error, source="steam_free.guild_scan", guild_id=guild.id)

    async def deliver(self, guild_id, channel, offer):
        key = (guild_id, offer.app_id, offer.ends)
        if key in self._pending_history:
            # Discord already accepted this message. Retry only the DB write.
            await asyncio.to_thread(
                self.history.record, guild_id, offer, self._pending_history[key]
            )
            del self._pending_history[key]
            return
        if offer.ends <= time.time() or await asyncio.to_thread(
            self.history.seen, guild_id, offer
        ):
            return
        embed, view = make_post(offer)
        # Discord.py handles Discord rate limits; never blindly resend on HTTP errors.
        message = await channel.send(
            embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none()
        )
        self._pending_history[key] = message.id
        try:
            await asyncio.to_thread(self.history.record, guild_id, offer, message.id)
        except sqlite3.Error as error:
            log_error(
                error,
                source="steam_free.save_post",
                guild_id=guild_id,
                channel_id=channel.id,
                reference=message.id,
            )
            return
        del self._pending_history[key]
        logger.info(
            "Steam offer posted guild=%s app=%s message=%s",
            guild_id,
            offer.app_id,
            message.id,
        )


async def setup(bot):
    if not RELEASED:
        raise RuntimeError("Steam alerts are unreleased and disabled.")
    await bot.add_cog(SteamFree(bot))
