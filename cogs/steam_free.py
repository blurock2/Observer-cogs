"""Automatic Steam free-to-keep promotions configured through /setup."""

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
RELEASED = True
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


def make_ended_post(original: discord.Embed, ends: int, price: str) -> discord.Embed:
    """Turn the original announcement into a closed promotion, retaining its artwork."""
    embed = discord.Embed.from_dict(original.to_dict())
    if not (embed.title or "").startswith("ENDED • "):
        embed.title = ("ENDED • " + (embed.title or "Steam offer"))[:256]
    embed.description = (
        "**This free-to-keep promotion has ended.** "
        "It can no longer be claimed for free."
    )
    embed.color = discord.Color(0x747982)
    for index, field in enumerate(embed.fields):
        if field.name == "Price":
            embed.set_field_at(
                index,
                name="Current price",
                value=discord.utils.escape_markdown(price)[:1000],
                inline=field.inline,
            )
        elif field.name == "Claim before":
            embed.set_field_at(
                index, name="Promotion ended",
                value=f"<t:{ends}:F>", inline=field.inline,
            )
    footer = embed.footer.text or "Observer • Steam offers"
    if not footer.endswith(" • Ended"):
        embed.set_footer(text=(footer + " • Ended")[:2048])
    return embed


class History:
    def __init__(self, path):
        self.path = path
        with closing(connect_sqlite(path)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS steam_free_posts ("
                "guild_id INTEGER, app_id INTEGER, ends INTEGER, message_id INTEGER, "
                "channel_id INTEGER, country TEXT, ended_at INTEGER, "
                "PRIMARY KEY(guild_id, app_id, ends))"
            )
            # Upgrade existing installations without losing post history.
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(steam_free_posts)")
            }
            for column, sql_type in (
                ("channel_id", "INTEGER"),
                ("country", "TEXT"),
                ("ended_at", "INTEGER"),
            ):
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE steam_free_posts ADD COLUMN {column} {sql_type}"
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

    def record(self, guild_id, offer, message_id, channel_id=None):
        with closing(connect_sqlite(self.path)) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO steam_free_posts "
                "(guild_id, app_id, ends, message_id, channel_id, country) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, offer.app_id, offer.ends, message_id, channel_id, offer.country),
            )
            conn.execute(
                "UPDATE steam_free_posts "
                "SET channel_id=COALESCE(channel_id, ?), country=COALESCE(country, ?) "
                "WHERE guild_id=? AND app_id=? AND ends=?",
                (channel_id, offer.country, guild_id, offer.app_id, offer.ends),
            )

    def pending_end_checks(self):
        with closing(connect_sqlite(self.path)) as conn:
            rows = conn.execute(
                "SELECT guild_id, app_id, ends, message_id, channel_id, country "
                "FROM steam_free_posts WHERE ended_at IS NULL "
                "ORDER BY ends"
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_ended(self, guild_id, app_id, ends):
        with closing(connect_sqlite(self.path)) as conn, conn:
            conn.execute(
                "UPDATE steam_free_posts SET ended_at=? "
                "WHERE guild_id=? AND app_id=? AND ends=? AND ended_at IS NULL",
                (int(time.time()), guild_id, app_id, ends),
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

    async def restored_price(self, app_id, country):
        """Return a verified nonzero current price, or None if still free/uncertain.

        Missing or changed store markup must never close an announcement.
        """
        source = await self.get(f"{STORE}/app/{app_id}/", cc=country, l="english")
        items = [item for item in store_items(source) if item.get("appid") == app_id]
        if not items:
            raise SteamResponseError(
                "Steam item data missing; cannot verify restored price"
            )
        if parse_offer(app_id, source, country) is not None:
            return None
        for item in items:
            if item.get("type") not in (0, 4):
                continue
            option = item.get("best_purchase_option")
            if not isinstance(option, dict):
                continue
            try:
                final = int(option["final_price_in_cents"])
                original = int(option["original_price_in_cents"])
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
            if final <= 0 or original <= 0:
                continue
            formatted = option.get("formatted_final_price")
            if isinstance(formatted, str):
                # Steam prices may contain simple HTML; never put that in Discord.
                formatted = re.sub(r"<[^>]*>", "", html.unescape(formatted)).strip()
                if formatted:
                    return formatted[:100]
            return "Paid (check Steam for the current amount)"
        return None

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
    """Automatic alerts configured through the existing setup dashboard."""

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
        for key, pending in list(self._pending_history.items()):
            guild_id, app_id, ends = key
            # Older in-process entries can contain just the message ID.
            if isinstance(pending, tuple):
                message_id, channel_id, country = pending
            else:
                message_id, channel_id, country = pending, None, "si"
            try:
                await asyncio.to_thread(
                    self.history.record,
                    guild_id,
                    Offer(app_id, "", "", ends, country),
                    message_id,
                    channel_id,
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
                country = str(config.get("country") or "si").lower()
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

        # Existing messages must be maintained even when new alerts are disabled.
        await self.update_ended_posts(client)

    async def update_ended_posts(self, client):
        """Edit posted messages once Steam confirms that their price is paid again."""
        try:
            pending = await asyncio.to_thread(self.history.pending_end_checks)
        except sqlite3.Error as error:
            log_error(error, source="steam_free.load_post_history")
            return

        price_cache = {}
        for post in pending:
            guild_id, app_id, ends = post["guild_id"], post["app_id"], post["ends"]
            try:
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    continue
                # Older DB rows have no channel or country. Fall back to /setup.
                config = await asyncio.to_thread(
                    self.bot.setup_store.get_module, guild_id, "steam_free"
                )
                country = post["country"] or str(config.get("country") or "si").lower()
                if not re.fullmatch(r"[a-z]{2}", country):
                    logger.warning("Invalid archived Steam region guild=%s", guild_id)
                    continue
                channel_id = post["channel_id"] or config.get("channel")
                channel = guild.get_channel(int(channel_id or 0))
                if not isinstance(channel, discord.TextChannel) or guild.me is None:
                    logger.warning(
                        "Steam posted channel unavailable guild=%s app=%s",
                        guild_id, app_id,
                    )
                    continue
                perms = channel.permissions_for(guild.me)
                if not (perms.view_channel and perms.read_message_history
                        and perms.embed_links):
                    logger.warning(
                        "Steam history permissions missing guild=%s channel=%s",
                        guild_id, channel.id,
                    )
                    continue

                cache_key = (app_id, country)
                if cache_key not in price_cache:
                    try:
                        price_cache[cache_key] = await client.restored_price(app_id, country)
                    except SteamResponseError:
                        logger.warning(
                            "Steam price check unavailable app=%s region=%s",
                            app_id, country, exc_info=True,
                        )
                        price_cache[cache_key] = None
                price = price_cache[cache_key]
                if price is None:
                    continue

                try:
                    message = await channel.fetch_message(post["message_id"])
                except discord.NotFound:
                    # Removed manually: stop trying to edit a nonexistent message.
                    await asyncio.to_thread(self.history.mark_ended, guild_id, app_id, ends)
                    logger.info("Steam post was deleted guild=%s app=%s", guild_id, app_id)
                    continue

                if (self.bot.user is None or message.author.id != self.bot.user.id
                        or not message.embeds
                        or not (message.embeds[0].footer.text or "").startswith(
                            "Observer • Steam offers"
                        )):
                    logger.warning(
                        "Steam post not recognized guild=%s app=%s message=%s",
                        guild_id, app_id, post["message_id"],
                    )
                    continue

                embed = make_ended_post(message.embeds[0], ends, price)
                await message.edit(
                    embed=embed, view=None, allowed_mentions=discord.AllowedMentions.none()
                )
                await asyncio.to_thread(self.history.mark_ended, guild_id, app_id, ends)
                logger.info(
                    "Steam offer ended; edited guild=%s app=%s message=%s",
                    guild_id, app_id, post["message_id"],
                )
            except (discord.Forbidden, discord.HTTPException) as error:
                logger.warning(
                    "Steam post could not be edited guild=%s app=%s: %s",
                    guild_id, app_id, type(error).__name__,
                )
            except Exception as error:  # noqa: BLE001 - isolate and report one history error
                log_error(
                    error, source="steam_free.edit_ended",
                    guild_id=guild_id, reference=post["message_id"],
                )

    async def deliver(self, guild_id, channel, offer):
        key = (guild_id, offer.app_id, offer.ends)
        if key in self._pending_history:
            # Discord already accepted this message. Retry only the DB write.
            pending = self._pending_history[key]
            if isinstance(pending, tuple):
                message_id, channel_id, _ = pending
            else:
                message_id, channel_id = pending, None
            await asyncio.to_thread(
                self.history.record, guild_id, offer, message_id, channel_id
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
        self._pending_history[key] = (message.id, channel.id, offer.country)
        try:
            await asyncio.to_thread(self.history.record, guild_id, offer, message.id, channel.id)
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
