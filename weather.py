from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore


logger = logging.getLogger(__name__)


# ============================================================ Constants and configuration

WEATHER_COLOR = discord.Color(0x96EDF1)
MODULE_KEY = "weather"

GEOCODING_URL = (
    "https://geocoding-api.open-meteo.com/v1/search"
)

FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast"
)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


# ============================================================ Cog

class Weather(commands.Cog):
    """
    Weather lookups through Open-Meteo.

    Slash command:
        /weather works in servers, bot DMs, and private channels.

    Server dashboard settings:
        weather.enabled
        weather.location
        weather.units
        weather.show_humidity
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.session: Optional[aiohttp.ClientSession] = None

    # ======================================================== Cog lifecycle

    async def cog_load(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=REQUEST_TIMEOUT,
            )

    async def cog_unload(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()

        self.session = None

    # ======================================================== Dashboard configuration helpers

    def _get(
        self,
        guild_id: int,
        key: str,
        default=None,
    ):
        return self.store.get(
            guild_id,
            MODULE_KEY,
            key,
            default,
        )

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(
            self._get(
                guild_id,
                "enabled",
                default=False,
            )
        )

    def _default_location(
        self,
        guild_id: int,
    ) -> Optional[str]:
        value = self._get(
            guild_id,
            "location",
            default=None,
        )

        if value is None:
            return None

        value = str(value).strip()

        return value or None

    def _units(self, guild_id: int) -> str:
        value = str(
            self._get(
                guild_id,
                "units",
                default="metric",
            )
        ).strip().lower()

        return "imperial" if value == "imperial" else "metric"

    def _show_humidity(self, guild_id: int) -> bool:
        return bool(
            self._get(
                guild_id,
                "show_humidity",
                default=True,
            )
        )

    # ======================================================== Open-Meteo API calls

    async def geocode_location(
        self,
        location: str,
    ) -> Optional[dict]:
        if self.session is None or self.session.closed:
            logger.error("Weather HTTP session is unavailable.")
            return None

        params = {
            "name": location,
            "count": 1,
            "language": "en",
            "format": "json",
        }

        try:
            async with self.session.get(
                GEOCODING_URL,
                params=params,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        except asyncio.TimeoutError:
            raise

        except aiohttp.ClientError:
            logger.exception(
                "Geocoding request failed for %s.",
                location,
            )
            raise

        results = data.get("results", [])

        if not results:
            return None

        return results[0]

    async def get_current_weather(
        self,
        latitude: float,
        longitude: float,
        units: str,
    ) -> Optional[dict]:
        if self.session is None or self.session.closed:
            logger.error("Weather HTTP session is unavailable.")
            return None

        is_imperial = units == "imperial"

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,"
                "relative_humidity_2m,"
                "apparent_temperature,"
                "precipitation,"
                "weather_code,"
                "wind_speed_10m"
            ),
            "temperature_unit": (
                "fahrenheit"
                if is_imperial
                else "celsius"
            ),
            "wind_speed_unit": (
                "mph"
                if is_imperial
                else "kmh"
            ),
            "precipitation_unit": (
                "inch"
                if is_imperial
                else "mm"
            ),
            "timezone": "auto",
        }

        try:
            async with self.session.get(
                FORECAST_URL,
                params=params,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        except asyncio.TimeoutError:
            raise

        except aiohttp.ClientError:
            logger.exception(
                "Weather request failed for coordinates %s, %s.",
                latitude,
                longitude,
            )
            raise

        return {
            "current": data.get("current"),
            "timezone": data.get("timezone"),
        }

    # ======================================================== Embed formatting

    def create_weather_embed(
        self,
        location: dict,
        weather_data: dict,
        requested_location: str,
        units: str,
        show_humidity: bool,
    ) -> discord.Embed:
        current = weather_data.get("current") or {}

        location_name = location.get(
            "name",
            requested_location,
        )

        country = location.get(
            "country",
            "",
        )

        timezone = weather_data.get(
            "timezone",
            location.get("timezone", ""),
        )

        title = f"Weather in {location_name}"

        if country:
            title += f", {country}"

        is_imperial = units == "imperial"

        temperature_unit = (
            "°F" if is_imperial else "°C"
        )

        wind_unit = (
            "mph" if is_imperial else "km/h"
        )

        precipitation_unit = (
            "in" if is_imperial else "mm"
        )

        condition = WEATHER_CODES.get(
            current.get("weather_code"),
            "Unknown conditions",
        )

        embed = discord.Embed(
            title=title,
            color=WEATHER_COLOR,
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(
            name="Condition",
            value=condition,
            inline=True,
        )

        embed.add_field(
            name="Temperature",
            value=(
                f"{current.get('temperature_2m', 'N/A')} "
                f"{temperature_unit}"
            ),
            inline=True,
        )

        embed.add_field(
            name="Feels like",
            value=(
                f"{current.get('apparent_temperature', 'N/A')} "
                f"{temperature_unit}"
            ),
            inline=True,
        )

        if show_humidity:
            embed.add_field(
                name="Humidity",
                value=(
                    f"{current.get('relative_humidity_2m', 'N/A')}%"
                ),
                inline=True,
            )

        embed.add_field(
            name="Wind",
            value=(
                f"{current.get('wind_speed_10m', 'N/A')} "
                f"{wind_unit}"
            ),
            inline=True,
        )

        embed.add_field(
            name="Precipitation",
            value=(
                f"{current.get('precipitation', 'N/A')} "
                f"{precipitation_unit}"
            ),
            inline=True,
        )

        if timezone:
            embed.set_footer(
                text=f"Timezone: {timezone} • Data: Open-Meteo"
            )
        else:
            embed.set_footer(
                text="Data: Open-Meteo"
            )

        return embed

    # ======================================================== Response builder

    async def build_weather_response(
        self,
        location_query: str,
        *,
        units: str,
        show_humidity: bool,
    ) -> tuple[Optional[discord.Embed], Optional[str]]:
        try:
            location = await self.geocode_location(
                location_query,
            )

            if location is None:
                return (
                    None,
                    (
                        f"I could not find **{location_query}**. "
                        "Try adding a country, for example "
                        "`Kranj, Slovenia`."
                    ),
                )

            weather_data = await self.get_current_weather(
                latitude=float(location["latitude"]),
                longitude=float(location["longitude"]),
                units=units,
            )

            if (
                weather_data is None
                or weather_data.get("current") is None
            ):
                return (
                    None,
                    "The weather service returned no current data.",
                )

            embed = self.create_weather_embed(
                location=location,
                weather_data=weather_data,
                requested_location=location_query,
                units=units,
                show_humidity=show_humidity,
            )

            return embed, None

        except asyncio.TimeoutError:
            return (
                None,
                "The weather service took too long to respond.",
            )

        except aiohttp.ClientError:
            return (
                None,
                "I could not connect to the weather service.",
            )

        except (KeyError, TypeError, ValueError):
            logger.exception(
                "Invalid weather data for %s.",
                location_query,
            )

            return (
                None,
                "The weather service returned invalid data.",
            )

    # ======================================================== Slash command

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
        name="weather",
        description="Show the current weather for a location.",
    )
    @app_commands.describe(
        location=(
            "Optional city or location. "
            "Uses Kranj, Slovenia when blank."
        ),
    )
    async def weather_command(
        self,
        interaction: discord.Interaction,
        location: Optional[str] = None,
    ) -> None:
        guild = interaction.guild

        # ==================================================== DM and private-channel context

        if guild is None:
            location_query = (
                location.strip()
                if location and location.strip()
                else "Kranj, Slovenia"
            )

            await interaction.response.defer()

            embed, error_message = (
                await self.build_weather_response(
                    location_query,
                    units="metric",
                    show_humidity=True,
                )
            )

            if error_message is not None:
                await interaction.followup.send(
                    error_message,
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        # ==================================================== Guild context

        if not self._is_enabled(guild.id):
            await interaction.response.send_message(
                (
                    "The Weather module is disabled. "
                    "Enable it through `/setup` first."
                ),
                ephemeral=True,
            )
            return

        location_query = (
            location.strip()
            if location is not None and location.strip()
            else self._default_location(guild.id)
        )

        if not location_query:
            await interaction.response.send_message(
                (
                    "Provide a location or set a default "
                    "location in `/setup`."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        embed, error_message = (
            await self.build_weather_response(
                location_query,
                units=self._units(guild.id),
                show_humidity=self._show_humidity(guild.id),
            )
        )

        if error_message is not None:
            await interaction.followup.send(
                error_message,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ======================================================== Plain-text message trigger

    def extract_city(
        self,
        content: str,
    ) -> Optional[str]:
        """
        Recognize simple weather questions.

        Examples:
            weather in Kranj
            what is the weather in Ljubljana?
            Kranj weather
        """
        cleaned = content.strip()

        patterns = (
            r"^weather\s+(?:in\s+)?(.+?)[?!,.]*$",
            (
                r"^what(?:\s+is|['’]?s)?\s+"
                r"(?:the\s+)?weather(?:\s+like)?\s+"
                r"in\s+(.+?)[?!,.]*$"
            ),
            (
                r"^how(?:\s+is|['’]?s)?\s+"
                r"(?:the\s+)?weather(?:\s+like)?\s+"
                r"in\s+(.+?)[?!,.]*$"
            ),
            r"^(.+?)\s+(?:weather|forecast)[?!,.]*$",
        )

        for pattern in patterns:
            match = re.match(
                pattern,
                cleaned,
                re.IGNORECASE,
            )

            if match:
                city = match.group(1).strip(
                    " \t\n,.:!?"
                )

                return city or None

        return None

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:
        """
        Answer plain-language weather prompts in servers only.

        This intentionally does not answer plain-text weather prompts
        in DMs. Use /weather in DMs.
        """
        if message.guild is None:
            return

        if message.author.bot:
            return

        if not self._is_enabled(message.guild.id):
            return

        city = self.extract_city(message.content)

        if city is None:
            return

        async with message.channel.typing():
            embed, error_message = (
                await self.build_weather_response(
                    city,
                    units=self._units(message.guild.id),
                    show_humidity=self._show_humidity(
                        message.guild.id,
                    ),
                )
            )

        if error_message is not None:
            await message.channel.send(
                error_message,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await message.channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ======================================================== Cog-level application-command error handling

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.exception(
            "Weather application command failed.",
            exc_info=error,
        )

        message = (
            "The weather command encountered an error. "
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
                "Could not send weather error response.",
            )


# ============================================================ Extension setup

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Weather(bot))