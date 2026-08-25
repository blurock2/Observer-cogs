from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.config import is_bot_owner
from cogs.mod_stats import ModerationStatsStore
from cogs.setup_ui import (
    DB_PATH,
    SetupConfigStore,
    owner_or_has_permissions,
)


DEFAULT_LOG_COLOR = 0x96EDF1
MODULE_KEY = "moderation"
LEGACY_CONFIG_PATH = "mod_config.json"


def _load_legacy_config() -> dict:
    if not os.path.exists(LEGACY_CONFIG_PATH):
        return {}

    try:
        with open(LEGACY_CONFIG_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


async def mod_perms_check(interaction: discord.Interaction) -> bool:
    """Allow configured moderators, head moderators, or the bot owner."""
    if interaction.guild is None:
        return False

    if is_bot_owner(interaction.user):
        return True

    cog = interaction.client.get_cog("ModerationCog")

    if not isinstance(cog, ModerationCog):
        return False

    return await cog._check_mod_perms(interaction)


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.mod_stats = ModerationStatsStore(DB_PATH)
        self._legacy = _load_legacy_config()

    # ============================================================ Confiiiiig

    def _legacy_guild_config(self, guild_id: int) -> dict:
        return self._legacy.get(str(guild_id), {})

    async def _get_guild_config(self, guild: discord.Guild) -> dict:
        guild_id = guild.id
        legacy = self._legacy_guild_config(guild_id)

        def pick(key: str, legacy_key: str):
            value = self.store.get(
                guild_id,
                MODULE_KEY,
                key,
            )

            if value is None:
                value = legacy.get(legacy_key)

            return value

        color = pick("embed_color", "embed_color")

        if color is None:
            color = DEFAULT_LOG_COLOR
        elif isinstance(color, str):
            try:
                color = int(color.strip().lstrip("#"), 16)
            except ValueError:
                color = DEFAULT_LOG_COLOR

        return {
            "log_channel": pick("log_channel", "log_channel"),
            "mod_role": pick("mod_role", "mod_role"),
            "head_mod_role": pick(
                "head_mod_role",
                "head_mod_role",
            ),
            "ping_mod_role": bool(
                self.store.get(
                    guild_id,
                    MODULE_KEY,
                    "ping_mod_role",
                    default=False,
                )
            ),
            "embed_color": int(color),
        }

    async def _is_logging_enabled(self, guild: discord.Guild) -> bool:
        return bool(
            self.store.get(
                guild.id,
                MODULE_KEY,
                "enabled",
                default=True,
            )
        )

    async def _get_moderator_role_ping(
        self,
        guild: discord.Guild,
    ) -> tuple[str, discord.AllowedMentions]:
        """
        Return the configured moderator-role mention when enabled.

        The role is read from moderation.mod_role and the toggle is read
        from moderation.ping_mod_role.
        """
        config = await self._get_guild_config(guild)

        if not config.get("ping_mod_role", False):
            return "", discord.AllowedMentions.none()

        role_id = config.get("mod_role")

        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            return "", discord.AllowedMentions.none()

        if role is None or role.is_default():
            return "", discord.AllowedMentions.none()

        return (
            role.mention,
            discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )

    async def _check_mod_perms(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            return False

        if not isinstance(interaction.user, discord.Member):
            return False

        config = await self._get_guild_config(interaction.guild)

        def has_role(role_id: object) -> bool:
            try:
                role_id = int(role_id)
            except (TypeError, ValueError):
                return False

            return any(
                role.id == role_id
                for role in interaction.user.roles
            )

        return (
            has_role(config.get("mod_role"))
            or has_role(config.get("head_mod_role"))
        )

    async def _head_mod_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            return False

        if is_bot_owner(interaction.user):
            return True

        if not isinstance(interaction.user, discord.Member):
            return False

        config = await self._get_guild_config(interaction.guild)

        try:
            head_mod_role_id = int(
                config.get("head_mod_role")
            )
        except (TypeError, ValueError):
            return False

        return any(
            role.id == head_mod_role_id
            for role in interaction.user.roles
        )

    async def _get_log_channel(
        self,
        guild: discord.Guild,
    ) -> Optional[discord.TextChannel]:
        if not await self._is_logging_enabled(guild):
            return None

        config = await self._get_guild_config(guild)

        try:
            log_channel_id = int(config.get("log_channel"))
        except (TypeError, ValueError):
            return None

        channel = guild.get_channel(log_channel_id)

        if not isinstance(channel, discord.TextChannel):
            return None

        return channel

    async def _send_mod_log(
        self,
        guild: discord.Guild,
        action: str,
        moderator: discord.Member,
        target: discord.Member,
        reason: Optional[str],
        undo_callback,
    ) -> None:
        """
        Send a normal moderation log.

        Normal moderation logs never ping the moderator role. Only report
        messages use the optional moderator-role notification.
        """
        channel = await self._get_log_channel(guild)

        if channel is None:
            return

        config = await self._get_guild_config(guild)

        embed = discord.Embed(
            title=f"{action} executed",
            color=config.get(
                "embed_color",
                DEFAULT_LOG_COLOR,
            ),
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(
            name="Moderator",
            value=(
                f"{moderator.mention} "
                f"(`{moderator}`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="Target",
            value=(
                f"{target.mention} "
                f"(`{target}`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="Reason",
            value=reason or "No reason provided",
            inline=False,
        )

        view = discord.ui.View(timeout=None)

        undo_button = discord.ui.Button(
            label="Undo",
            style=discord.ButtonStyle.danger,
        )

        undo_button.callback = undo_callback
        view.add_item(undo_button)

        try:
            await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def send_report_log(
        self,
        guild: discord.Guild,
        embed: discord.Embed,
        channel: Optional[discord.TextChannel] = None,
    ) -> bool:
        """
        Send a report embed and optionally ping the moderator role.

        If `channel` is provided, the report is sent there. Otherwise the
        configured moderation log channel is used.

        Returns True when Discord accepts the message.
        """
        if not await self._is_logging_enabled(guild):
            return False

        if channel is None:
            channel = await self._get_log_channel(guild)

        if not isinstance(channel, discord.TextChannel):
            return False

        content, allowed_mentions = (
            await self._get_moderator_role_ping(guild)
        )

        try:
            await channel.send(
                content=content or None,
                embed=embed,
                allowed_mentions=allowed_mentions,
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    # ============================================================ Location checker

    async def _validate_target(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        action: str,
    ) -> bool:
        if interaction.guild is None:
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Could not verify your server permissions.",
                ephemeral=True,
            )
            return False

        if member == interaction.user:
            await interaction.response.send_message(
                f"You cannot {action} yourself.",
                ephemeral=True,
            )
            return False

        if member.guild_permissions >= interaction.user.guild_permissions:
            await interaction.response.send_message(
                (
                    f"You cannot {action} someone with equal or "
                    "higher permissions."
                ),
                ephemeral=True,
            )
            return False

        bot_member = interaction.guild.me

        if (
            bot_member is not None
            and member.top_role >= bot_member.top_role
        ):
            await interaction.response.send_message(
                (
                    "I cannot act on that member because their top "
                    "role is equal to or higher than my top role."
                ),
                ephemeral=True,
            )
            return False

        return True

    # ============================================================ The purge!!!!

    @app_commands.command(
        name="purge",
        description="Delete recent messages from this channel.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        amount="Number of messages to delete, from 1 to 1000.",
        reason="Reason for deleting the messages.",
    )
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: int,
        reason: Optional[str] = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Could not verify your server permissions.",
                ephemeral=True,
            )
            return

        if not isinstance(
            interaction.channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                (
                    "This command can only be used in a normal "
                    "text channel."
                ),
                ephemeral=True,
            )
            return

        if amount < 1 or amount > 1000:
            await interaction.response.send_message(
                "The amount must be between 1 and 1000.",
                ephemeral=True,
            )
            return

        bot_member = interaction.guild.me

        if bot_member is None:
            await interaction.response.send_message(
                "I could not find my member information in this server.",
                ephemeral=True,
            )
            return

        permissions = interaction.channel.permissions_for(
            bot_member
        )

        required = {
            "View Channel": permissions.view_channel,
            "Read Message History": (
                permissions.read_message_history
            ),
            "Manage Messages": permissions.manage_messages,
            "Send Messages": permissions.send_messages,
        }

        missing = [
            name
            for name, allowed in required.items()
            if not allowed
        ]

        if missing:
            await interaction.response.send_message(
                (
                    "I am missing these permissions in this channel: "
                    + ", ".join(missing)
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            deleted_messages = await interaction.channel.purge(
                limit=amount,
                reason=reason or f"Purge by {interaction.user}",
            )

            async def undo_purge(
                undo_interaction: discord.Interaction,
            ) -> None:
                await undo_interaction.response.send_message(
                    "A purge cannot be undone.",
                    ephemeral=True,
                )

            await self._send_mod_log(
                guild=interaction.guild,
                action="Purge",
                moderator=interaction.user,
                target=interaction.user,
                reason=reason,
                undo_callback=undo_purge,
            )

            await interaction.followup.send(
                f"Deleted {len(deleted_messages)} message(s).",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                (
                    "I do not have permission to delete messages "
                    "in this channel."
                ),
                ephemeral=True,
            )

        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the purge request: `{error}`",
                ephemeral=True,
            )

    # ============================================================ Ban

    @app_commands.command(
        name="ban",
        description="Ban a member from the server.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        member="Member to ban.",
        reason="Reason for the ban.",
    )
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: Optional[str] = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not await self._validate_target(
            interaction,
            member,
            "ban",
        ):
            return

        if not isinstance(interaction.user, discord.Member):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await member.ban(reason=reason)

            self.mod_stats.increment(
                interaction.guild.id,
                interaction.user.id,
                "bans",
            )

            async def undo_ban(
                undo_interaction: discord.Interaction,
            ) -> None:
                if not await self._head_mod_check(
                    undo_interaction
                ):
                    await undo_interaction.response.send_message(
                        (
                            "Only the head of moderation can "
                            "undo actions."
                        ),
                        ephemeral=True,
                    )
                    return

                try:
                    await interaction.guild.unban(
                        member,
                        reason=(
                            f"Ban undone by "
                            f"{undo_interaction.user}"
                        ),
                    )

                    await undo_interaction.response.send_message(
                        f"Unbanned `{member}`.",
                        ephemeral=True,
                    )

                except discord.HTTPException:
                    await undo_interaction.response.send_message(
                        "Failed to unban the user.",
                        ephemeral=True,
                    )

            await self._send_mod_log(
                guild=interaction.guild,
                action="Ban",
                moderator=interaction.user,
                target=member,
                reason=reason,
                undo_callback=undo_ban,
            )

            await interaction.followup.send(
                f"Banned `{member}`.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to ban that member.",
                ephemeral=True,
            )

        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the ban request: `{error}`",
                ephemeral=True,
            )

    # ============================================================ Kick aka "Boot"

    @app_commands.command(
        name="kick",
        description="Kick a member from the server.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        member="Member to kick.",
        reason="Reason for the kick.",
    )
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: Optional[str] = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not await self._validate_target(
            interaction,
            member,
            "kick",
        ):
            return

        if not isinstance(interaction.user, discord.Member):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await member.kick(reason=reason)

            self.mod_stats.increment(
                interaction.guild.id,
                interaction.user.id,
                "kicks",
            )

            async def undo_kick(
                undo_interaction: discord.Interaction,
            ) -> None:
                if not await self._head_mod_check(
                    undo_interaction
                ):
                    await undo_interaction.response.send_message(
                        (
                            "Only the head of moderation can "
                            "undo actions."
                        ),
                        ephemeral=True,
                    )
                    return

                await undo_interaction.response.send_message(
                    (
                        "A kick cannot be undone automatically. "
                        "The user must rejoin the server."
                    ),
                    ephemeral=True,
                )

            await self._send_mod_log(
                guild=interaction.guild,
                action="Kick",
                moderator=interaction.user,
                target=member,
                reason=reason,
                undo_callback=undo_kick,
            )

            await interaction.followup.send(
                f"Kicked `{member}`.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to kick that member.",
                ephemeral=True,
            )

        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the kick request: `{error}`",
                ephemeral=True,
            )

    # ============================================================ Timeout stuff

    @app_commands.command(
        name="timeout",
        description="Timeout a member.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        member="Member to timeout.",
        duration_minutes=(
            "Timeout duration in minutes, from 1 to 40320."
        ),
        reason="Reason for the timeout.",
    )
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration_minutes: int,
        reason: Optional[str] = None,
    ) -> None:
        if interaction.guild is None:
            return

        if duration_minutes < 1 or duration_minutes > 40320:
            await interaction.response.send_message(
                (
                    "The timeout must be between 1 and 40320 "
                    "minutes."
                ),
                ephemeral=True,
            )
            return

        if not await self._validate_target(
            interaction,
            member,
            "timeout",
        ):
            return

        if not isinstance(interaction.user, discord.Member):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            timeout_until = (
                discord.utils.utcnow()
                + timedelta(minutes=duration_minutes)
            )

            await member.timeout(
                timeout_until,
                reason=reason,
            )

            self.mod_stats.increment(
                interaction.guild.id,
                interaction.user.id,
                "timeouts",
            )

            async def undo_timeout(
                undo_interaction: discord.Interaction,
            ) -> None:
                if not await self._head_mod_check(
                    undo_interaction
                ):
                    await undo_interaction.response.send_message(
                        (
                            "Only the head of moderation can "
                            "undo actions."
                        ),
                        ephemeral=True,
                    )
                    return

                try:
                    await member.timeout(
                        None,
                        reason=(
                            f"Timeout undone by "
                            f"{undo_interaction.user}"
                        ),
                    )

                    await undo_interaction.response.send_message(
                        f"Removed timeout from {member.mention}.",
                        ephemeral=True,
                    )

                except discord.HTTPException:
                    await undo_interaction.response.send_message(
                        "Failed to remove the timeout.",
                        ephemeral=True,
                    )

            await self._send_mod_log(
                guild=interaction.guild,
                action="Timeout",
                moderator=interaction.user,
                target=member,
                reason=reason,
                undo_callback=undo_timeout,
            )

            await interaction.followup.send(
                (
                    f"Timed out {member.mention} for "
                    f"{duration_minutes} minute(s)."
                ),
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                (
                    "I do not have permission to timeout "
                    "that member."
                ),
                ephemeral=True,
            )

        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the timeout request: `{error}`",
                ephemeral=True,
            )

    # ============================================================ Mod chud stats

    @app_commands.command(
        name="modstats",
        description="View moderation statistics.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        moderator="Leave blank to view your own statistics.",
    )
    async def modstats(
        self,
        interaction: discord.Interaction,
        moderator: Optional[discord.Member] = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Could not verify your server permissions.",
                ephemeral=True,
            )
            return

        target = moderator or interaction.user
        stats = self.mod_stats.get(
            interaction.guild.id,
            target.id,
        )

        total_actions = sum(stats.values())

        embed = discord.Embed(
            title=(
                f"Moderation Stats — "
                f"{target.display_name}"
            ),
            color=DEFAULT_LOG_COLOR,
        )

        embed.set_thumbnail(
            url=target.display_avatar.url
        )

        embed.add_field(
            name="Reports claimed",
            value=str(stats["reports_claimed"]),
            inline=True,
        )

        embed.add_field(
            name="Kicks",
            value=str(stats["kicks"]),
            inline=True,
        )

        embed.add_field(
            name="Bans",
            value=str(stats["bans"]),
            inline=True,
        )

        embed.add_field(
            name="Timeouts",
            value=str(stats["timeouts"]),
            inline=True,
        )

        embed.add_field(
            name="Total actions",
            value=str(total_actions),
            inline=True,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


    # ============================================================ Error handling

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = (
                "You need the configured Moderator or Head "
                "Moderator role to use that command."
            )
        else:
            print(
                f"[moderation] "
                f"{type(error).__name__}: {error}"
            )
            message = (
                "An unexpected moderation error occurred."
            )

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
    await bot.add_cog(ModerationCog(bot))