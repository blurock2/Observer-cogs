from __future__ import annotations

import asyncio
import logging
import re
import ssl
from io import BytesIO
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

# Reuse the same config store the setup dashboard writes to, so the
# "Spotify" module's Enabled toggle actually controls this cog.
from cogs.setup_ui import SetupConfigStore, DB_PATH


logger = logging.getLogger(__name__)


# ============================================================ Constants and configuration

SPOTIFY_COLOR = discord.Color(0x1DB954)
MODULE_KEY = "spotify"

SPOTIFY_LOGO_URL = (
    "https://storage.googleapis.com/pr-newsroom-wp/1/2018/11/"
    "Spotify_Logo_RGB_Green.png"
)

SPOTIFY_OEMBED_URL = "https://open.spotify.com/oembed"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


# Matches:
# https://open.spotify.com/track/TRACK_ID
# https://open.spotify.com/track/TRACK_ID?si=...
#
# spotify.link URLs are redirect URLs and usually cannot be treated
# as direct track URLs without resolving the redirect first.
SPOTIFY_TRACK_PATTERN = re.compile(
    r"https?://open\.spotify\.com/track/"
    r"([A-Za-z0-9]+)"
    r"(?:\?[^\s<>]*)?",
    re.IGNORECASE,
)


# This is only a workaround for local certificate problems.
# Proper certificate verification is preferable.
SPOTIFY_SSL_CONTEXT = ssl.create_default_context()
SPOTIFY_SSL_CONTEXT.check_hostname = False
SPOTIFY_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Cover-art color extraction: skip palette colors that are basically
# black or basically white, since those make for a bad embed accent.
COVER_COLOR_MIN_BRIGHTNESS = 30
COVER_COLOR_MAX_BRIGHTNESS = 235


# ============================================================ Cog

class SpotifyShow(commands.Cog):
    """
    Spotify track embed cog.

    Servers:
        Detect Spotify track links in messages, delete the original
        message, and post a Discord embed. Controlled by the
        "Spotify" module's Enabled toggle in /setup.

    DMs and private channels:
        Use /spotify with a Spotify track URL. Not guild-scoped, so
        the dashboard toggle does not apply there.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        # Same on-disk store the setup dashboard reads and writes.
        self.store = SetupConfigStore(DB_PATH)

    # ============================================================ Cog lifecycle

    async def cog_load(self) -> None:
        """
        Create one reusable aiohttp session for this cog.
        """
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=REQUEST_TIMEOUT,
                connector=aiohttp.TCPConnector(
                    ssl=SPOTIFY_SSL_CONTEXT,
                ),
            )

    async def cog_unload(self) -> None:
        """
        Close the aiohttp session when the cog is unloaded.
        """
        if self.session is not None and not self.session.closed:
            await self.session.close()

        self.session = None

    # ============================================================ Dashboard toggle helper

    def is_enabled(self, guild_id: int) -> bool:
        """
        Read the "Spotify" module's Enabled toggle from the setup
        dashboard. Defaults to True, matching the module's default.
        """
        return bool(self.store.get(guild_id, MODULE_KEY, "enabled", default=True))

    # ============================================================ Spotify URL parsing

    def extract_spotify_track(
        self,
        content: str,
    ) -> Optional[tuple[str, str]]:
        """
        Extract a Spotify track ID and the matching URL from text.

        Returns:
            (track_id, original_url), or None if no direct Spotify
            track URL was found.
        """
        match = SPOTIFY_TRACK_PATTERN.search(content)

        if match is None:
            return None

        track_id = match.group(1)
        original_url = match.group(0)

        return track_id, original_url

    # ============================================================ Spotify oEmbed request

    async def get_track_info(
        self,
        track_id: str,
        original_url: str,
    ) -> Optional[dict[str, Optional[str]]]:
        """
        Fetch track information through Spotify oEmbed.

        Spotify oEmbed does not provide a separate artist field,
        so the returned artist value is None.
        """
        if self.session is None or self.session.closed:
            logger.error("Spotify session is unavailable.")
            return None

        spotify_track_url = (
            f"https://open.spotify.com/track/{track_id}"
        )

        params = {
            "url": spotify_track_url,
            "format": "json",
        }

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }

        try:
            async with self.session.get(
                SPOTIFY_OEMBED_URL,
                params=params,
                headers=headers,
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Spotify oEmbed returned HTTP %s for track %s.",
                        response.status,
                        track_id,
                    )
                    return None

                data = await response.json()

        except asyncio.TimeoutError:
            logger.warning(
                "Spotify oEmbed request timed out for track %s.",
                track_id,
            )
            return None

        except aiohttp.ClientError:
            logger.exception(
                "Spotify oEmbed request failed for track %s.",
                track_id,
            )
            return None

        except Exception:
            logger.exception(
                "Unexpected Spotify error for track %s.",
                track_id,
            )
            return None

        if not isinstance(data, dict):
            logger.warning(
                "Spotify returned a non-object response for track %s.",
                track_id,
            )
            return None

        title = data.get("title")
        thumbnail_url = data.get("thumbnail_url")
        version = data.get("version")

        if version != "1.0" or not title:
            logger.warning(
                "Invalid Spotify oEmbed data for track %s: %r",
                track_id,
                data,
            )
            return None

        return {
            "name": str(title).strip(),
            "artist": None,
            "image_url": (
                str(thumbnail_url)
                if thumbnail_url
                else None
            ),
            "external_url": original_url,
        }

    # ============================================================ Cover-art color extraction

    async def _fetch_image_bytes(self, image_url: str) -> Optional[bytes]:
        """
        Download the cover-art image so its dominant color can be
        extracted. Returns None on any failure.
        """
        if self.session is None or self.session.closed:
            return None

        try:
            async with self.session.get(image_url) as response:
                if response.status != 200:
                    return None
                return await response.read()

        except asyncio.TimeoutError:
            logger.warning("Timed out fetching Spotify cover art.")
            return None

        except aiohttp.ClientError:
            logger.exception("Failed to fetch Spotify cover art.")
            return None

    def _extract_dominant_color(self, image_bytes: bytes) -> Optional[discord.Color]:
        """
        Pick a representative accent color from the cover art.

        Downscales the image and reduces it to a small palette, then
        picks the most common palette color, skipping near-black and
        near-white entries so the embed doesn't end up looking bland.
        This is blocking/CPU-bound and should be run off the event
        loop (see get_track_info's caller).
        """
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                img = img.convert("RGB")
                img.thumbnail((100, 100))

                paletted = img.quantize(colors=5)
                palette = paletted.getpalette()
                color_counts = sorted(paletted.getcolors(), reverse=True)

                fallback = None
                for count, index in color_counts:
                    r, g, b = palette[index * 3: index * 3 + 3]
                    if fallback is None:
                        fallback = (r, g, b)

                    brightness = (r + g + b) / 3
                    if COVER_COLOR_MIN_BRIGHTNESS <= brightness <= COVER_COLOR_MAX_BRIGHTNESS:
                        return discord.Color.from_rgb(r, g, b)

                if fallback is not None:
                    return discord.Color.from_rgb(*fallback)

        except Exception:
            logger.exception("Failed to extract dominant color from cover art.")

        return None

    async def get_cover_color(self, image_url: Optional[str]) -> discord.Color:
        """
        Resolve the embed accent color from the track's cover art,
        falling back to Spotify green if there is no artwork or
        extraction fails for any reason.
        """
        if not image_url:
            return SPOTIFY_COLOR

        image_bytes = await self._fetch_image_bytes(image_url)
        if image_bytes is None:
            return SPOTIFY_COLOR

        color = await asyncio.to_thread(self._extract_dominant_color, image_bytes)
        return color if color is not None else SPOTIFY_COLOR

    # ============================================================ Embed creation

    async def create_spotify_embed(
        self,
        track_info: dict[str, Optional[str]],
    ) -> discord.Embed:
        """
        Create a Discord embed for a Spotify track, colored to match
        its cover art.
        """
        name = track_info.get("name") or "Unknown Track"
        artist = track_info.get("artist")
        image_url = track_info.get("image_url")
        external_url = track_info.get("external_url")

        color = await self.get_cover_color(image_url)

        embed = discord.Embed(
            title=name,
            url=external_url,
            color=color,
            description="Click the title to open in Spotify.",
        )

        if artist:
            embed.set_author(
                name=f"Spotify • {artist}",
                icon_url=SPOTIFY_LOGO_URL,
            )
        else:
            embed.set_thumbnail(url=SPOTIFY_LOGO_URL)

        if image_url:
            embed.set_image(url=image_url)

        embed.set_footer(
            text="Powered by Spotify",
            icon_url=SPOTIFY_LOGO_URL,
        )

        return embed

    # ============================================================ Automatic server message handling

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Automatically replace Spotify track links in servers.

        DMs are intentionally ignored here. Use /spotify in DMs.
        """
        if message.author.bot:
            return

        if message.guild is None:
            return

        if not self.is_enabled(message.guild.id):
            return

        result = self.extract_spotify_track(message.content)

        if result is None:
            return

        track_id, original_url = result

        track_info = await self.get_track_info(
            track_id,
            original_url,
        )

        if track_info is None:
            return

        embed = await self.create_spotify_embed(track_info)

        try:
            await message.delete()

        except discord.Forbidden:
            await message.channel.send(
                "I need the Manage Messages permission to replace "
                "Spotify links.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        except discord.NotFound:
            # The message was already deleted.
            pass

        except discord.HTTPException:
            logger.exception(
                "Could not delete Spotify message %s.",
                message.id,
            )
            return

        try:
            await message.channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except discord.Forbidden:
            logger.warning(
                "Missing permission to send Spotify embed in channel %s.",
                message.channel.id,
            )

        except discord.HTTPException:
            logger.exception(
                "Could not send Spotify embed in channel %s.",
                message.channel.id,
            )

    # ============================================================ Slash command

    @app_commands.allowed_installs(
        guilds=True,
        users=True,
    )
    @app_commands.allowed_contexts(
        guilds=True,
        dms=True,
        private_channels=True,
    )
    @app_commands.command(
        name="spotify",
        description="Embed a Spotify track by URL.",
    )
    @app_commands.describe(
        url="Spotify track URL",
    )
    async def spotify_command(
        self,
        interaction: discord.Interaction,
        url: str,
    ) -> None:
        """
        Embed a Spotify track in a server, bot DM, or private channel.

        In a server, this respects the "Spotify" module's Enabled
        toggle in /setup. DMs and private channels are unaffected.
        """
        if interaction.guild is not None and not self.is_enabled(interaction.guild.id):
            await interaction.response.send_message(
                "The Spotify module is disabled on this server. "
                "An admin can turn it on via `/setup`.",
                ephemeral=True,
            )
            return

        result = self.extract_spotify_track(url)

        if result is None:
            await interaction.response.send_message(
                "That does not look like a valid Spotify track URL. "
                "Use a direct track link such as "
                "`https://open.spotify.com/track/...`.",
                ephemeral=True,
            )
            return

        track_id, original_url = result

        await interaction.response.defer()

        track_info = await self.get_track_info(
            track_id,
            original_url,
        )

        if track_info is None:
            await interaction.followup.send(
                "I could not fetch information for that Spotify track. "
                "It may be unavailable, region-restricted, or Spotify "
                "may be temporarily unavailable.",
                ephemeral=True,
            )
            return

        embed = await self.create_spotify_embed(track_info)

        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ============================================================ Slash-command error handler

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """
        Handle errors raised by this cog's application command.
        """
        logger.exception(
            "Spotify application command failed.",
            exc_info=error,
        )

        message = (
            "The Spotify command encountered an error. "
            "Check the bot console for details."
        )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )

        except discord.HTTPException:
            logger.exception(
                "Could not send Spotify error response."
            )


# ============================================================ Extension setup

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpotifyShow(bot))