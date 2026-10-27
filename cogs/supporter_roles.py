"""Keep Ko-fi supporter recognition roles permanently in the support server."""
from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from cogs.config import OBSERVER_SUPPORT_GUILD_ID
from cogs.setup_ui import DB_PATH
from database import connect_sqlite

logger = logging.getLogger("observer.supporter_roles")


class SupporterRoleKeeper(commands.Cog):
    """Restore either Ko-fi supporter role if an integration removes it."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.role_names = {
            "Supporter <3",
            "Monthly Supporter <3",
            "Tier 1 - Cookie :3",
            "Tier 2 - Cutie <3",
            "Tier 3 - Goated",
            os.getenv("OBSERVER_KOFI_ONE_TIME_ROLE_NAME", "Supporter <3").strip(),
            os.getenv("OBSERVER_KOFI_TIER_1_ROLE_NAME", "Tier 1 - Cookie :3").strip(),
            os.getenv("OBSERVER_KOFI_TIER_2_ROLE_NAME", "Tier 2 - Cutie <3").strip(),
            os.getenv("OBSERVER_KOFI_TIER_3_ROLE_NAME", "Tier 3 - Goated").strip(),
        }
        self.role_names.add(
            os.getenv("OBSERVER_KOFI_MONTHLY_ROLE_NAME", "Monthly Supporter <3").strip()
        )
        self.role_names.discard("")
        with connect_sqlite(DB_PATH) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS activity_supporter_roles (
                    guild_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, member_id, role_id)
                )"""
            )

    def _remember(self, guild_id: int, member_id: int, role_id: int) -> None:
        with connect_sqlite(DB_PATH) as db:
            db.execute(
                "INSERT OR IGNORE INTO activity_supporter_roles (guild_id, member_id, role_id) VALUES (?, ?, ?)",
                (guild_id, member_id, role_id),
            )

    def _remembered_roles(self, guild_id: int, member_id: int) -> list[int]:
        with connect_sqlite(DB_PATH) as db:
            rows = db.execute(
                "SELECT role_id FROM activity_supporter_roles WHERE guild_id = ? AND member_id = ?",
                (guild_id, member_id),
            ).fetchall()
        return [int(row["role_id"]) for row in rows]

    async def _restore(self, member: discord.Member, role_ids: list[int], reason: str) -> None:
        roles = [
            role for role_id in role_ids
            if (role := member.guild.get_role(role_id)) is not None
            and role not in member.roles
        ]
        if not roles:
            return
        try:
            await member.add_roles(*roles, reason=reason)
        except discord.HTTPException:
            logger.warning(
                "Could not restore supporter roles for member %s in guild %s; check Observer's Manage Roles permission and role order",
                member.id,
                member.guild.id,
                exc_info=True,
            )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != OBSERVER_SUPPORT_GUILD_ID:
            return
        supporter_roles = {role.id for role in after.guild.roles if role.name in self.role_names}
        if not supporter_roles:
            return

        before_roles = {role.id for role in before.roles}
        after_roles = {role.id for role in after.roles}
        added = supporter_roles & (after_roles - before_roles)
        removed = supporter_roles & (before_roles - after_roles)

        for role_id in added:
            self._remember(after.guild.id, after.id, role_id)
        if removed:
            # Keep history even if this was the first event Observer saw for a
            # member who already had the role when this cog was deployed.
            for role_id in removed:
                self._remember(after.guild.id, after.id, role_id)
            await self._restore(after, sorted(removed), "Preserve permanent Ko-fi supporter recognition")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != OBSERVER_SUPPORT_GUILD_ID:
            return
        await self._restore(
            member,
            self._remembered_roles(member.guild.id, member.id),
            "Restore permanent Ko-fi supporter recognition",
        )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        guild = self.bot.get_guild(OBSERVER_SUPPORT_GUILD_ID)
        if guild is None:
            return
        supporter_role_ids = {
            role.id for role in guild.roles if role.name in self.role_names
        }
        with connect_sqlite(DB_PATH) as db:
            existing_supporters = [
                (guild.id, member.id, role.id)
                for member in guild.members
                for role in member.roles
                if role.id in supporter_role_ids
            ]
            db.executemany(
                "INSERT OR IGNORE INTO activity_supporter_roles (guild_id, member_id, role_id) VALUES (?, ?, ?)",
                existing_supporters,
            )
            rows = db.execute(
                "SELECT member_id, role_id FROM activity_supporter_roles WHERE guild_id = ?",
                (guild.id,),
            ).fetchall()
        for row in rows:
            member_id = int(row["member_id"])
            member = guild.get_member(member_id)
            if member is None:
                try:
                    member = await guild.fetch_member(member_id)
                except discord.NotFound:
                    continue
                except discord.HTTPException:
                    logger.warning("Could not look up supporter %s during role reconciliation", member_id, exc_info=True)
                    continue
            await self._restore(member, [int(row["role_id"])], "Restore permanent Ko-fi supporter recognition")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SupporterRoleKeeper(bot))
