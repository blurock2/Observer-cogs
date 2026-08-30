from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.abc import User as DiscordUser
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

    @staticmethod
    def _debug_avatar_target(label: str, target: object) -> None:
        print(
            f"[avatar-debug] {label}: type={type(target).__name__}, "
            f"is_user={isinstance(target, discord.User)}, "
            f"is_abc_user={isinstance(target, DiscordUser)}"
        )

    async def _resolve_user_for_details(
        self,
        target: discord.abc.User,
    ) -> discord.abc.User:
        try:
            if isinstance(target, discord.Member):
                return await self.bot.fetch_user(target.id)
            if isinstance(target, discord.User):
                return await self.bot.fetch_user(target.id)
        except discord.HTTPException:
            pass
        return target

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

    @app_commands.command(name="avatar")
    @app_commands.describe(
        member="User whose avatar you want to view.",
    )
    async def avatar_slash(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.User] = None,
    ) -> None:
        target = member or interaction.user
        self._debug_avatar_target("slash avatar target", target)

        if not isinstance(target, DiscordUser):
            print(
                f"[avatar-debug] rejecting slash avatar target: "
                f"{type(target).__name__}"
            )
            return

        display_name = getattr(target, "display_name", target.name)

        embed = discord.Embed(
            title=f"{display_name}'s Avatar",
            color=EMBED_COLOR,
        )
        embed.set_image(url=target.display_avatar.url)
        embed.set_footer(text=f"User ID: {target.id}")

        await interaction.response.send_message(embed=embed)

    @commands.command(name="avatar")
    async def avatar_prefix(
        self,
        ctx: commands.Context,
        member: Optional[discord.User] = None,
    ) -> None:
        target = member or ctx.author
        self._debug_avatar_target("prefix avatar target", target)

        if not isinstance(target, DiscordUser):
            print(
                f"[avatar-debug] rejecting prefix avatar target: "
                f"{type(target).__name__}"
            )
            return

        display_name = getattr(target, "display_name", target.name)

        embed = discord.Embed(
            title=f"{display_name}'s Avatar",
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
    @app_commands.describe(
        member="User to look up.",
        show_all="Include the user's available profile details with the ID.",
    )
    async def user_id(
        self,
        ctx: commands.Context,
        member: Optional[discord.User] = None,
        show_all: bool = False,
    ) -> None:
        guild = ctx.guild

        if guild is not None:
            if not await self._require_enabled(ctx):
                return

            target = member or ctx.author

            if not isinstance(target, discord.Member):
                return

            if show_all:
                user_for_details = await self._resolve_user_for_details(target)
                details = []
                username = getattr(user_for_details, "name", None)
                global_name = getattr(user_for_details, "global_name", None)
                nickname = getattr(target, "nick", None) or getattr(target, "display_name", None)
                mention = getattr(target, "mention", None)
                user_id_value = getattr(target, "id", None)

                if username:
                    details.append(f"**Username:** `{username}`")
                if global_name:
                    details.append(f"**Global name:** `{global_name}`")
                if nickname and nickname != username:
                    details.append(f"**Nickname:** `{nickname}`")
                if user_id_value is not None:
                    details.append(f"**User ID:** `{user_id_value}`")
                if mention:
                    details.append(f"**Mention:** {mention}")

                banner = getattr(user_for_details, "banner", None)
                banner_url = banner.url if banner is not None else None
                avatar_url = getattr(user_for_details.display_avatar, "url", None)

                embed = discord.Embed(
                    title=f"{target.display_name}'s ID and profile",
                    description="\n".join(details) if details else "No extra profile data available.",
                    color=EMBED_COLOR,
                )
                if avatar_url:
                    embed.set_thumbnail(url=avatar_url)
                if banner_url:
                    embed.set_image(url=banner_url)
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
                embed.set_footer(text=f"{guild.name} • ID information")

                await ctx.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
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
            return

        target = member or ctx.author

        if not isinstance(target, DiscordUser):
            print(
                f"[id-debug] rejecting DM target: {type(target).__name__}"
            )
            return

        if show_all:
            user_for_details = await self._resolve_user_for_details(target)
            details = []
            username = getattr(user_for_details, "name", None)
            global_name = getattr(user_for_details, "global_name", None)
            display_name = getattr(target, "display_name", None) or getattr(user_for_details, "name", None)
            mention = getattr(target, "mention", None) or getattr(user_for_details, "mention", None)
            user_id_value = getattr(target, "id", None) or getattr(user_for_details, "id", None)

            if username:
                details.append(f"**Username:** `{username}`")
            if global_name:
                details.append(f"**Global name:** `{global_name}`")
            if display_name and display_name != username:
                details.append(f"**Display name:** `{display_name}`")
            if user_id_value is not None:
                details.append(f"**User ID:** `{user_id_value}`")
            if mention:
                details.append(f"**Mention:** {mention}")

            banner = getattr(user_for_details, "banner", None)
            banner_url = banner.url if banner is not None else None
            avatar_url = getattr(user_for_details.display_avatar, "url", None)

            embed = discord.Embed(
                title=f"{display_name or username or 'User'}'s ID and profile",
                description="\n".join(details) if details else "No extra profile data available.",
                color=EMBED_COLOR,
            )
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            if banner_url:
                embed.set_image(url=banner_url)

            await ctx.send(embed=embed)
            return

        await ctx.send(f"`{target.id}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemberCommands(bot))