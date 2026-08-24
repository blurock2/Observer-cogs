from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore


DATABASE_PATH = Path("data/member_stats.sqlite3")
EMBED_COLOR = 0x96EDF1
MODULE_KEY = "member_commands"


class MemberCommands(commands.Cog):
    """
    Member information commands.

    Dashboard settings:
    - member_commands.enabled
    - member_commands.show_join_date
    - member_commands.show_roles
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)

        DATABASE_PATH.parent.mkdir(exist_ok=True)

        self.database = sqlite3.connect(
            DATABASE_PATH,
            check_same_thread=False,
        )
        self.database.execute(
            """
            CREATE TABLE IF NOT EXISTS message_stats (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        self.database.commit()

    def cog_unload(self) -> None:
        self.database.close()

    # ============================================================ Dashboard config

    def _get(self, guild_id: int, key: str, default=None):
        return self.store.get(guild_id, MODULE_KEY, key, default)

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "enabled", default=False))

    def _show_join_date(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "show_join_date", default=True))

    def _show_roles(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "show_roles", default=True))

    async def _require_enabled(
        self,
        ctx: commands.Context,
    ) -> bool:
        if ctx.guild is None:
            await ctx.send(
                embed=self.error_embed(
                    "This command can only be used inside a server."
                )
            )
            return False

        if self._is_enabled(ctx.guild.id):
            return True

        await ctx.send(
            embed=self.error_embed(
                "Member Commands is disabled. Enable it through `/setup` first."
            )
        )
        return False

    # ============================================================ Utilities

    @staticmethod
    def error_embed(message: str) -> discord.Embed:
        return discord.Embed(
            description=f"⚠️ {message}",
            color=EMBED_COLOR,
        )

    @staticmethod
    def timestamp(date) -> str:
        return f"<t:{int(date.timestamp())}:F>"

    def get_message_count(self, guild_id: int, user_id: int) -> int:
        result = self.database.execute(
            """
            SELECT message_count
            FROM message_stats
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()

        return int(result[0]) if result else 0

    # ============================================================ Message tracking

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        if not self._is_enabled(message.guild.id):
            return

        self.database.execute(
            """
            INSERT INTO message_stats (guild_id, user_id, message_count)
            VALUES (?, ?, 1)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET message_count = message_count + 1
            """,
            (message.guild.id, message.author.id),
        )
        self.database.commit()

    # ============================================================ Avatar

    @commands.hybrid_command(
        name="avatar",
        description="Show a member's avatar.",
    )
    @commands.guild_only()
    async def avatar(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        if not await self._require_enabled(ctx):
            return

        target = member or ctx.author

        if not isinstance(target, discord.Member):
            return

        embed = discord.Embed(
            title=f"{target.display_name}'s Avatar",
            color=EMBED_COLOR,
        )
        embed.set_image(url=target.display_avatar.url)
        embed.set_footer(text=f"User ID: {target.id}")

        await ctx.send(embed=embed)

    # ============================================================ Profile

    @commands.hybrid_command(
        name="profile",
        description="Show a member's profile.",
    )
    @commands.guild_only()
    async def profile(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        if not await self._require_enabled(ctx):
            return

        guild = ctx.guild

        if guild is None:
            return

        target = member or ctx.author

        if not isinstance(target, discord.Member):
            return

        message_count = self.get_message_count(guild.id, target.id)

        embed = discord.Embed(
            title=f"{target.display_name}'s Profile",
            color=EMBED_COLOR,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(
            name="Username",
            value=f"`{target}`",
            inline=True,
        )
        embed.add_field(
            name="User ID",
            value=f"`{target.id}`",
            inline=True,
        )
        embed.add_field(
            name="Tracked Messages",
            value=f"{message_count:,}",
            inline=True,
        )
        embed.add_field(
            name="Account Created",
            value=self.timestamp(target.created_at),
            inline=False,
        )

        if self._show_join_date(guild.id):
            embed.add_field(
                name="Joined This Server",
                value=(
                    self.timestamp(target.joined_at)
                    if target.joined_at
                    else "Unknown"
                ),
                inline=False,
            )

        if self._show_roles(guild.id):
            roles = [
                role.mention
                for role in reversed(target.roles)
                if role != guild.default_role
            ]

            role_text = ", ".join(roles) if roles else "No roles"

            if len(role_text) > 1024:
                role_text = role_text[:1021] + "..."

            embed.add_field(
                name=f"Roles ({len(roles)})",
                value=role_text,
                inline=False,
            )

        embed.set_footer(text=f"{guild.name} • Member profile")

        await ctx.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ============================================================ IDs

    @commands.hybrid_command(
        name="id",
        description="Show a member's Discord and server IDs.",
    )
    @commands.guild_only()
    async def user_id(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        if not await self._require_enabled(ctx):
            return

        guild = ctx.guild

        if guild is None:
            return

        target = member or ctx.author

        if not isinstance(target, discord.Member):
            return

        embed = discord.Embed(
            title=f"{target.display_name}'s IDs",
            description=(
                "Useful identifiers for this member and the current server."
            ),
            color=EMBED_COLOR,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(
            name="👤 User ID",
            value=f"`{target.id}`",
            inline=False,
        )
        embed.add_field(
            name="🏠 Server ID",
            value=f"`{guild.id}`",
            inline=False,
        )
        embed.add_field(
            name="🎭 Highest Role ID",
            value=f"`{target.top_role.id}`",
            inline=False,
        )
        embed.add_field(
            name="📛 Mention",
            value=target.mention,
            inline=False,
        )
        embed.set_footer(text=f"{guild.name} • ID information")

        await ctx.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemberCommands(bot))