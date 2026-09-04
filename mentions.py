from __future__ import annotations

from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore


MODULE_KEY = "mentions"
NO_MENTIONS = discord.AllowedMentions.none()

DEFAULT_TIMEOUT_MINUTES = 60
DEFAULT_WARNING_DELETE_DELAY = 8
MAX_TIMEOUT_MINUTES = 40320


class Mentions(commands.Cog):
    """
    Mention-protection moderation system.

    Dashboard settings:
    - mentions.enabled
    - mentions.blocked_role
    - mentions.whitelist_role (sender bypass role)
    - mentions.timeout_minutes
    - mentions.delete_warning_after
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)

    # ============================================================ Setup config

    def _get(self, guild_id: int, key: str, default=None):
        return self.store.get(guild_id, MODULE_KEY, key, default)

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "enabled", default=False))

    def _get_role_id(
        self,
        guild_id: int,
        key: str,
    ) -> Optional[int]:
        value = self._get(guild_id, key)

        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _get_timeout_minutes(self, guild_id: int) -> int:
        value = self._get(
            guild_id,
            "timeout_minutes",
            default=DEFAULT_TIMEOUT_MINUTES,
        )

        try:
            return max(
                0,
                min(MAX_TIMEOUT_MINUTES, int(value)),
            )
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_MINUTES

    def _get_warning_delete_delay(
        self,
        guild_id: int,
    ) -> Optional[float]:
        value = self._get(
            guild_id,
            "delete_warning_after",
            default=DEFAULT_WARNING_DELETE_DELAY,
        )

        try:
            seconds = int(value)
        except (TypeError, ValueError):
            seconds = DEFAULT_WARNING_DELETE_DELAY

        return float(seconds) if seconds > 0 else None

    # ============================================================ Violation checks

    @staticmethod
    def _has_role(
        member: discord.Member,
        role_id: int,
    ) -> bool:
        return any(role.id == role_id for role in member.roles)

    def _get_violation_reason(
        self,
        message: discord.Message,
        blocked_role_id: int,
        bypass_role_id: Optional[int],
    ) -> Optional[str]:
        """Return a reason when a protected mention is found."""
        author = message.author
        can_ping_protected = self._has_role(author, blocked_role_id) or (
            bypass_role_id is not None
            and self._has_role(author, bypass_role_id)
        )

        if can_ping_protected:
            return None

        for mentioned_member in message.mentions:
            if not self._has_role(mentioned_member, blocked_role_id):
                continue

            if (
                bypass_role_id is not None
                and self._has_role(mentioned_member, bypass_role_id)
            ):
                continue

            return (
                "pinging protected member "
                f"{mentioned_member.mention}"
            )

        for mentioned_role in message.role_mentions:
            if mentioned_role.id == blocked_role_id:
                return (
                    "pinging protected role "
                    f"{mentioned_role.name}"
                )

        return None

    # ============================================================ Listener

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        guild = message.guild

        if not self._is_enabled(guild.id):
            return

        blocked_role_id = self._get_role_id(guild.id, "blocked_role")

        if blocked_role_id is None:
            return

        bypass_role_id = self._get_role_id(
            guild.id,
            "whitelist_role",
        )

        violation_reason = self._get_violation_reason(
            message,
            blocked_role_id,
            bypass_role_id,
        )

        if violation_reason is None:
            return

        member = message.author

        try:
            await message.delete()
        except discord.Forbidden:
            print(
                "[mentions] Could not delete a protected-mention "
                "violation: missing Manage Messages."
            )
        except discord.HTTPException as error:
            print(f"[mentions] Could not delete message: {error}")

        timeout_succeeded = False
        timeout_minutes = self._get_timeout_minutes(guild.id)

        if (
            timeout_minutes > 0
            and isinstance(member, discord.Member)
        ):
            try:
                await member.timeout(
                    timedelta(minutes=timeout_minutes),
                    reason=(
                        "Mention protection violation: "
                        f"{violation_reason}"
                    ),
                )
                timeout_succeeded = True
            except discord.Forbidden:
                print(
                    "[mentions] Could not timeout "
                    f"{member} ({member.id}): missing permission or "
                    "role hierarchy prevents it."
                )
            except discord.HTTPException as error:
                print(
                    "[mentions] Discord rejected timeout for "
                    f"{member} ({member.id}): {error}"
                )

        if timeout_succeeded:
            warning = (
                f"{member.mention}, your message was removed and you "
                f"were timed out for {timeout_minutes} minute(s) because "
                "you pinged a protected member or role."
            )
        elif timeout_minutes == 0:
            warning = (
                f"{member.mention}, your message was removed because "
                "you pinged a protected member or role."
            )
        else:
            warning = (
                f"{member.mention}, your message was removed because "
                "you pinged a protected member or role. I could not "
                "apply the configured timeout."
            )

        try:
            await message.channel.send(
                warning,
                delete_after=self._get_warning_delete_delay(guild.id),
                allowed_mentions=NO_MENTIONS,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Mentions(bot))