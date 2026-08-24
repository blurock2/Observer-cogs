from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import (
    DB_PATH,
    SetupConfigStore,
    owner_or_has_permissions,
)


CONFIG_FILE = Path("message_relay_config.json")
MODULE_KEY = "message_relay"
EMBED_COLOR = discord.Colour(0x96EDF1)
NO_MENTIONS = discord.AllowedMentions.none()
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024


def load_config() -> dict:
    """
    Cross-server mappings, intentionally stored separately from generic
    dashboard settings.

    {
        "links": [
            {"source_guild": 123, "target_guilds": [456, 789]}
        ],
        "sources": {
            "123": [source_channel_id]
        },
        "targets": {
            "123": [target_channel_id]
        }
    }
    """
    if not CONFIG_FILE.exists():
        return {"links": [], "sources": {}, "targets": {}}

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

        if not isinstance(data, dict):
            raise ValueError("Relay config is not a dictionary")

        data.setdefault("links", [])
        data.setdefault("sources", {})
        data.setdefault("targets", {})

        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return {"links": [], "sources": {}, "targets": {}}


def save_config(data: dict) -> None:
    CONFIG_FILE.write_text(
        json.dumps(data, indent=4),
        encoding="utf-8",
    )


def get_source_guilds_for_target(
    data: dict,
    target_guild_id: int,
) -> list[int]:
    result = []

    for link in data["links"]:
        if target_guild_id in link.get("target_guilds", []):
            result.append(int(link["source_guild"]))

    return result


def get_target_guilds_for_source(
    data: dict,
    source_guild_id: int,
) -> list[int]:
    for link in data["links"]:
        if int(link.get("source_guild", 0)) == source_guild_id:
            return [
                int(guild_id)
                for guild_id in link.get("target_guilds", [])
            ]

    return []


class DestinationSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "MessageRelay",
        message: discord.Message,
    ):
        self.cog = cog
        self.message = message

        data = load_config()
        source_guild_id = message.guild.id
        target_guild_ids = get_target_guilds_for_source(
            data,
            source_guild_id,
        )

        target_channel_ids = data["targets"].get(
            str(source_guild_id),
            [],
        )

        options: list[discord.SelectOption] = []

        for raw_channel_id in target_channel_ids:
            try:
                channel_id = int(raw_channel_id)
            except (TypeError, ValueError):
                continue

            channel = cog.bot.get_channel(channel_id)

            if (
                isinstance(channel, discord.TextChannel)
                and channel.guild.id in target_guild_ids
            ):
                options.append(
                    discord.SelectOption(
                        label=f"{channel.guild.name} / #{channel.name}"[:100],
                        value=str(channel.id),
                    )
                )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No destinations configured",
                    value="none",
                )
            )

        super().__init__(
            placeholder="Select destination channel...",
            options=options[:25],
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if self.values[0] == "none":
            await interaction.response.edit_message(
                content="No destination channels are configured.",
                view=None,
            )
            return

        try:
            channel_id = int(self.values[0])
        except ValueError:
            await interaction.response.edit_message(
                content="That channel selection is invalid.",
                view=None,
            )
            return

        destination = self.cog.bot.get_channel(channel_id)

        if not isinstance(destination, discord.TextChannel):
            await interaction.response.edit_message(
                content="That channel is unavailable.",
                view=None,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await self.cog.forward_message(self.message, destination)

            await interaction.edit_original_response(
                content=f"Message sent to {destination.mention}.",
                view=None,
            )
        except discord.HTTPException as error:
            await interaction.edit_original_response(
                content=f"Failed to send message: `{error}`",
                view=None,
            )


class DestinationView(discord.ui.View):
    def __init__(
        self,
        cog: "MessageRelay",
        message: discord.Message,
    ):
        super().__init__(timeout=120)
        self.add_item(DestinationSelect(cog, message))


class MessageRelay(commands.Cog):
    """
    Manual cross-server message relay.

    Dashboard settings:
    - message_relay.enabled
    - message_relay.relay_bots
    - message_relay.filter

    Cross-server server/channel links remain in message_relay_config.json
    because a generic per-guild setup panel cannot represent arbitrary
    multiple server and multiple channel mappings.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)

        self.context_menu = app_commands.ContextMenu(
            name="Relay message",
            callback=self.relay_message,
        )

        self.bot.tree.add_command(self.context_menu)

    def cog_unload(self) -> None:
        self.bot.tree.remove_command(
            self.context_menu.name,
            type=self.context_menu.type,
        )

    # ============================================================ Dashboard config

    def _get(self, guild_id: int, key: str, default=None):
        return self.store.get(guild_id, MODULE_KEY, key, default)

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "enabled", default=False))

    def _relay_bots(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "relay_bots", default=False))

    def _filtered_words(self, guild_id: int) -> list[str]:
        raw = self._get(guild_id, "filter", default="")

        if raw is None:
            return []

        return [
            word.strip().lower()
            for word in str(raw).split(",")
            if word.strip()
        ]

    def _is_filtered(
        self,
        guild_id: int,
        content: str,
    ) -> bool:
        lowered_content = content.lower()

        return any(
            word in lowered_content
            for word in self._filtered_words(guild_id)
        )

    # ============================================================ Context menu

    async def relay_message(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        if message.guild is None:
            await interaction.response.send_message(
                "This can only be used in servers.",
                ephemeral=True,
            )
            return

        guild = message.guild

        if not self._is_enabled(guild.id):
            await interaction.response.send_message(
                "Message Relay is disabled in this server. "
                "Enable it through `/setup` first.",
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "You need the **Manage Messages** permission to relay messages.",
                ephemeral=True,
            )
            return

        if message.author.bot and not self._relay_bots(guild.id):
            await interaction.response.send_message(
                "Relaying bot messages is disabled in `/setup`.",
                ephemeral=True,
            )
            return

        if self._is_filtered(guild.id, message.content):
            await interaction.response.send_message(
                "This message contains a filtered word and cannot be relayed.",
                ephemeral=True,
            )
            return

        data = load_config()
        source_guild_id = guild.id

        target_guilds = get_target_guilds_for_source(
            data,
            source_guild_id,
        )

        if not target_guilds:
            await interaction.response.send_message(
                "This server is not configured as a relay source server.",
                ephemeral=True,
            )
            return

        source_channels = data["sources"].get(
            str(source_guild_id),
            [],
        )

        source_channel_ids = {
            int(channel_id)
            for channel_id in source_channels
        }

        if message.channel.id not in source_channel_ids:
            await interaction.response.send_message(
                "This channel is not configured as a relay source channel.",
                ephemeral=True,
            )
            return

        targets = data["targets"].get(str(source_guild_id), [])

        if not targets:
            await interaction.response.send_message(
                "No destination channels are configured for this server.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Where should this message be sent?",
            view=DestinationView(self, message),
            ephemeral=True,
        )

    # ============================================================ Cross-server setup

    @app_commands.command(
        name="relay_setup",
        description="Link one source server to up to four target servers.",
    )
    @app_commands.describe(
        source_server_id="ID of the source/staff server.",
        target_server_1="ID of the first target server.",
        target_server_2="ID of the optional second target server.",
        target_server_3="ID of the optional third target server.",
        target_server_4="ID of the optional fourth target server.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def relay_setup(
        self,
        interaction: discord.Interaction,
        source_server_id: str,
        target_server_1: str,
        target_server_2: Optional[str] = None,
        target_server_3: Optional[str] = None,
        target_server_4: Optional[str] = None,
    ) -> None:
        raw_ids = [
            source_server_id,
            target_server_1,
            target_server_2,
            target_server_3,
            target_server_4,
        ]

        raw_ids = [guild_id for guild_id in raw_ids if guild_id]

        guilds: list[discord.Guild] = []

        for raw_id in raw_ids:
            try:
                guild_id = int(raw_id)
            except ValueError:
                await interaction.response.send_message(
                    "All server IDs must be numeric.",
                    ephemeral=True,
                )
                return

            guild = self.bot.get_guild(guild_id)

            if guild is None:
                await interaction.response.send_message(
                    f"The bot is not in the server with ID `{raw_id}`.",
                    ephemeral=True,
                )
                return

            guilds.append(guild)

        source_guild = guilds[0]
        target_guilds = guilds[1:]

        if not target_guilds:
            await interaction.response.send_message(
                "At least one target server is required.",
                ephemeral=True,
            )
            return

        if len({guild.id for guild in guilds}) != len(guilds):
            await interaction.response.send_message(
                "The source and target servers must all be different.",
                ephemeral=True,
            )
            return

        data = load_config()

        data["links"] = [
            link
            for link in data["links"]
            if int(link.get("source_guild", 0)) != source_guild.id
        ]

        data["links"].append(
            {
                "source_guild": source_guild.id,
                "target_guilds": [
                    guild.id for guild in target_guilds
                ],
            }
        )

        save_config(data)

        target_names = ", ".join(
            guild.name for guild in target_guilds
        )

        await interaction.response.send_message(
            "Relay link configured:\n"
            f"- Source server: **{source_guild.name}**\n"
            f"- Target servers: **{target_names}**\n\n"
            "Use `/relay_add_source` in the source server and "
            "`/relay_add` in each target server.",
            ephemeral=True,
        )

    # ============================================================ Target channels

    @app_commands.command(
        name="relay_add",
        description="Add a destination relay channel in this target server.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def relay_add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            return

        data = load_config()

        source_guild_ids = get_source_guilds_for_target(
            data,
            interaction.guild.id,
        )

        if not source_guild_ids:
            await interaction.response.send_message(
                "This server is not configured as a relay target server.",
                ephemeral=True,
            )
            return

        # One target server can appear in multiple mappings. This version
        # associates the channel with the first configured source mapping.
        source_guild_id = source_guild_ids[0]

        targets = data["targets"].get(str(source_guild_id), [])

        normalized_targets = [str(channel_id) for channel_id in targets]

        if str(channel.id) not in normalized_targets:
            normalized_targets.append(str(channel.id))
            data["targets"][str(source_guild_id)] = normalized_targets
            save_config(data)

        await interaction.response.send_message(
            f"Added {channel.mention} as a relay destination for the "
            f"source server ID `{source_guild_id}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="relay_remove",
        description="Remove a destination relay channel.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def relay_remove(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            return

        data = load_config()

        source_guild_ids = get_source_guilds_for_target(
            data,
            interaction.guild.id,
        )

        if not source_guild_ids:
            await interaction.response.send_message(
                "This server is not configured as a relay target server.",
                ephemeral=True,
            )
            return

        source_guild_id = source_guild_ids[0]
        targets = [
            str(channel_id)
            for channel_id in data["targets"].get(
                str(source_guild_id),
                [],
            )
        ]

        if str(channel.id) not in targets:
            await interaction.response.send_message(
                f"{channel.mention} is not configured as a relay destination.",
                ephemeral=True,
            )
            return

        targets.remove(str(channel.id))
        data["targets"][str(source_guild_id)] = targets
        save_config(data)

        await interaction.response.send_message(
            f"Removed {channel.mention} from relay destinations.",
            ephemeral=True,
        )

    # ============================================================ Source channels

    @app_commands.command(
        name="relay_add_source",
        description="Add a source/draft channel in this source server.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def relay_add_source(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            return

        data = load_config()

        target_guild_ids = get_target_guilds_for_source(
            data,
            interaction.guild.id,
        )

        if not target_guild_ids:
            await interaction.response.send_message(
                "This server is not configured as a relay source server.",
                ephemeral=True,
            )
            return

        sources = [
            str(channel_id)
            for channel_id in data["sources"].get(
                str(interaction.guild.id),
                [],
            )
        ]

        if str(channel.id) not in sources:
            sources.append(str(channel.id))
            data["sources"][str(interaction.guild.id)] = sources
            save_config(data)

        await interaction.response.send_message(
            f"Added {channel.mention} as a relay source channel.",
            ephemeral=True,
        )

    @app_commands.command(
        name="relay_remove_source",
        description="Remove a source/draft relay channel.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def relay_remove_source(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            return

        data = load_config()

        target_guild_ids = get_target_guilds_for_source(
            data,
            interaction.guild.id,
        )

        if not target_guild_ids:
            await interaction.response.send_message(
                "This server is not configured as a relay source server.",
                ephemeral=True,
            )
            return

        sources = [
            str(channel_id)
            for channel_id in data["sources"].get(
                str(interaction.guild.id),
                [],
            )
        ]

        if str(channel.id) not in sources:
            await interaction.response.send_message(
                f"{channel.mention} is not configured as a relay source channel.",
                ephemeral=True,
            )
            return

        sources.remove(str(channel.id))
        data["sources"][str(interaction.guild.id)] = sources
        save_config(data)

        await interaction.response.send_message(
            f"Removed {channel.mention} from relay source channels.",
            ephemeral=True,
        )

    # ============================================================ Forwarding

    async def forward_message(
        self,
        message: discord.Message,
        destination: discord.TextChannel,
    ) -> None:
        """Forward text and eligible attachments as a non-pinging embed."""
        embed = discord.Embed(
            description=message.content[:4096] or "*No text content*",
            colour=EMBED_COLOR,
            timestamp=message.created_at,
        )
        embed.set_author(
            name=str(message.author),
            icon_url=message.author.display_avatar.url,
            url=message.jump_url,
        )
        embed.set_footer(
            text=(
                f"Relayed from {message.guild.name} "
                f"• #{message.channel.name}"
            )
        )

        files: list[discord.File] = []
        first_image_filename: Optional[str] = None

        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for index, attachment in enumerate(message.attachments):
                if attachment.size > MAX_ATTACHMENT_SIZE:
                    continue

                try:
                    async with session.get(attachment.url) as response:
                        if response.status != 200:
                            continue

                        file_data = await response.read()
                except aiohttp.ClientError:
                    continue

                filename = f"{index}_{attachment.filename}"

                files.append(
                    discord.File(
                        io.BytesIO(file_data),
                        filename=filename,
                    )
                )

                if (
                    first_image_filename is None
                    and attachment.content_type
                    and attachment.content_type.startswith("image/")
                ):
                    first_image_filename = filename

        if first_image_filename is not None:
            embed.set_image(
                url=f"attachment://{first_image_filename}"
            )

        await destination.send(
            embed=embed,
            files=files,
            allowed_mentions=NO_MENTIONS,
        )

    # ============================================================ Errors

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = (
                "You need **Manage Server** permission to configure relay links."
            )
        else:
            print(
                f"[message_relay] {type(error).__name__}: {error}"
            )
            message = "An unexpected relay error occurred."

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageRelay(bot))