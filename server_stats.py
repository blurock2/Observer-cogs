from __future__ import annotations

import asyncio
from typing import Optional

import discord
from discord.ext import commands, tasks

from cogs.setup_ui import DB_PATH, SetupConfigStore


MODULE_KEY = "server_stats"

TOTAL_CHANNEL_KEY = "total_channel_id"
ONLINE_CHANNEL_KEY = "online_channel_id"

DEFAULT_INTERVAL_MINUTES = 10
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 60


class ServerInfo(commands.Cog):
    """
    Server-info command plus live voice-channel statistics.

    Setup Dashboard settings used:
    - server_stats.enabled
    - server_stats.category
    - server_stats.show_total
    - server_stats.show_online
    - server_stats.update_interval

    Internal stored values:
    - server_stats.total_channel_id
    - server_stats.online_channel_id
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)

        # Prevent duplicate refreshes for the same guild.
        self._locks: dict[int, asyncio.Lock] = {}

        self.stats_updater.start()

    def cog_unload(self) -> None:
        self.stats_updater.cancel()

    # ============================================================ Config helpers

    def _get(self, guild_id: int, key: str, default=None):
        return self.store.get(guild_id, MODULE_KEY, key, default)

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "enabled", False))

    def _get_category_id(self, guild_id: int) -> Optional[int]:
        value = self._get(guild_id, "category")

        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _show_total(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "show_total", True))

    def _show_online(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "show_online", True))

    def _get_interval_minutes(self, guild_id: int) -> int:
        value = self._get(
            guild_id,
            "update_interval",
            DEFAULT_INTERVAL_MINUTES,
        )

        try:
            return max(
                MIN_INTERVAL_MINUTES,
                min(MAX_INTERVAL_MINUTES, int(value)),
            )
        except (TypeError, ValueError):
            return DEFAULT_INTERVAL_MINUTES

    def _get_counter_channel_id(
        self,
        guild_id: int,
        key: str,
    ) -> Optional[int]:
        value = self._get(guild_id, key)

        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _get_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._locks.get(guild_id)

        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock

        return lock

    # ============================================================ Stat calculations

    @staticmethod
    def _online_count(guild: discord.Guild) -> int:
        """
        Count members whose status is not offline.

        Accurate online counts require the guild presences intent in
        both the Developer Portal and your bot's Intents setup.
        """
        return sum(
            1
            for member in guild.members
            if not member.bot and member.status is not discord.Status.offline
        )

    @staticmethod
    def _total_count(guild: discord.Guild) -> int:
        """Count all members, including bots, from Discord's guild count."""
        return guild.member_count or len(guild.members)

    # ============================================================ Counter channels

    async def _get_or_create_counter(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        config_key: str,
        name: str,
    ) -> Optional[discord.VoiceChannel]:
        """
        Find the previously created counter channel or create it under
        the configured category.
        """
        channel_id = self._get_counter_channel_id(guild.id, config_key)

        if channel_id is not None:
            channel = guild.get_channel(channel_id)

            if isinstance(channel, discord.VoiceChannel):
                if channel.category_id != category.id:
                    try:
                        await channel.edit(
                            category=category,
                            reason="Server stats category changed",
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                return channel

        try:
            channel = await guild.create_voice_channel(
                name=name,
                category=category,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=True,
                        connect=False,
                    ),
                },
                reason="Creating server statistics counter",
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            print(
                f"[server_stats] Could not create counter in "
                f"{guild.name} ({guild.id}): {error}"
            )
            return None

        self.store.set(guild.id, MODULE_KEY, config_key, channel.id)
        return channel

    async def _delete_counter(
        self,
        guild: discord.Guild,
        config_key: str,
    ) -> None:
        """Delete a counter that was disabled in the setup dashboard."""
        channel_id = self._get_counter_channel_id(guild.id, config_key)

        if channel_id is None:
            return

        channel = guild.get_channel(channel_id)

        if isinstance(channel, discord.VoiceChannel):
            try:
                await channel.delete(
                    reason="Server statistics counter disabled",
                )
            except (discord.Forbidden, discord.HTTPException):
                return

        self.store.set(guild.id, MODULE_KEY, config_key, None)

    async def _rename_counter(
        self,
        channel: discord.VoiceChannel,
        name: str,
    ) -> None:
        """Rename only when the counter value has actually changed."""
        if channel.name == name:
            return

        try:
            await channel.edit(
                name=name,
                reason="Updating server statistics counter",
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def refresh_guild_stats(
        self,
        guild: discord.Guild,
    ) -> None:
        """Create, update, or remove counters for one server."""
        if not self._is_enabled(guild.id):
            return

        category_id = self._get_category_id(guild.id)

        if category_id is None:
            return

        category = guild.get_channel(category_id)

        if not isinstance(category, discord.CategoryChannel):
            return

        lock = self._get_lock(guild.id)

        async with lock:
            show_total = self._show_total(guild.id)
            show_online = self._show_online(guild.id)

            if not show_total and not show_online:
                return

            if show_total:
                total = self._total_count(guild)
                total_name = f"👥 Total Members: {total:,}"

                total_channel = await self._get_or_create_counter(
                    guild,
                    category,
                    TOTAL_CHANNEL_KEY,
                    total_name,
                )

                if total_channel is not None:
                    await self._rename_counter(total_channel, total_name)
            else:
                await self._delete_counter(guild, TOTAL_CHANNEL_KEY)

            if show_online:
                online = self._online_count(guild)
                online_name = f"🟢 Online Members: {online:,}"

                online_channel = await self._get_or_create_counter(
                    guild,
                    category,
                    ONLINE_CHANNEL_KEY,
                    online_name,
                )

                if online_channel is not None:
                    await self._rename_counter(online_channel, online_name)
            else:
                await self._delete_counter(guild, ONLINE_CHANNEL_KEY)

    # ============================================================ Automatic updates

    @tasks.loop(minutes=1)
    async def stats_updater(self) -> None:
        """
        Check each server once per minute but only update a guild once
        its own configured update interval has elapsed.
        """
        for guild in self.bot.guilds:
            if not self._is_enabled(guild.id):
                continue

            category_id = self._get_category_id(guild.id)

            if category_id is None:
                continue

            interval = self._get_interval_minutes(guild.id)
            last_update = self._get(guild.id, "last_update", 0)

            now = discord.utils.utcnow().timestamp()

            try:
                last_update = float(last_update)
            except (TypeError, ValueError):
                last_update = 0

            if now - last_update < interval * 60:
                continue

            try:
                await self.refresh_guild_stats(guild)
                self.store.set(guild.id, MODULE_KEY, "last_update", now)
            except Exception as error:
                print(
                    f"[server_stats] Refresh error for "
                    f"{guild.name} ({guild.id}): {error}"
                )

    @stats_updater.before_loop
    async def before_stats_updater(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if self._is_enabled(member.guild.id):
            await self.refresh_guild_stats(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if self._is_enabled(member.guild.id):
            await self.refresh_guild_stats(member.guild)

    @commands.Cog.listener()
    async def on_presence_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        """
        Refresh when online/offline state changes.

        This listener requires `presences=True` in your bot Intents and
        Presence Intent enabled in Discord's Developer Portal.
        """
        if before.status == after.status:
            return

        if self._is_enabled(after.guild.id):
            await self.refresh_guild_stats(after.guild)

    # ============================================================ Existing server command

    @staticmethod
    def limit_text(text: str, limit: int = 1000) -> str:
        text = str(text)

        if len(text) <= limit:
            return text

        return text[:limit - 3] + "..."

    @staticmethod
    def comma_separated_chunks(
        items: list[str],
        limit: int = 1024,
    ) -> list[str]:
        if not items:
            return ["None"]

        chunks: list[str] = []
        current_chunk = ""

        for item in items:
            if not current_chunk:
                current_chunk = item
                continue

            possible_chunk = f"{current_chunk}, {item}"

            if len(possible_chunk) <= limit:
                current_chunk = possible_chunk
            else:
                chunks.append(current_chunk)
                current_chunk = item

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    @commands.command(
        name="server",
        aliases=("serverinfo", "guildinfo"),
    )
    @commands.guild_only()
    async def server(self, ctx: commands.Context) -> None:
        """Display detailed information about the current server."""
        guild = ctx.guild

        if guild is None:
            return

        visible_channels = [
            channel
            for channel in guild.channels
            if channel.permissions_for(ctx.author).view_channel
        ]

        categories = [
            channel
            for channel in visible_channels
            if isinstance(channel, discord.CategoryChannel)
        ]

        text_channels = [
            channel
            for channel in visible_channels
            if isinstance(channel, discord.TextChannel)
            and not channel.is_news()
        ]

        announcement_channels = [
            channel
            for channel in visible_channels
            if isinstance(channel, discord.TextChannel)
            and channel.is_news()
        ]

        forum_channels = [
            channel
            for channel in visible_channels
            if isinstance(channel, discord.ForumChannel)
        ]

        voice_channels = [
            channel
            for channel in visible_channels
            if isinstance(channel, discord.VoiceChannel)
            and not isinstance(channel, discord.StageChannel)
        ]

        stage_channels = [
            channel
            for channel in visible_channels
            if isinstance(channel, discord.StageChannel)
        ]

        roles = [
            role
            for role in guild.roles
            if not role.is_default()
        ]

        humans_cached = sum(
            1
            for member in guild.members
            if not member.bot
        )

        bots_cached = sum(
            1
            for member in guild.members
            if member.bot
        )

        owner = guild.owner
        owner_text = (
            owner.mention
            if owner
            else f"Unknown (`{guild.owner_id}`)"
        )

        verification_level = (
            guild.verification_level.name
            .replace("_", " ")
            .title()
        )

        notification_level = (
            guild.default_notifications.name
            .replace("_", " ")
            .title()
        )

        created_at = discord.utils.format_dt(
            guild.created_at,
            style="F",
        )
        created_relative = discord.utils.format_dt(
            guild.created_at,
            style="R",
        )

        features = [
            feature.replace("_", " ").title()
            for feature in guild.features
        ]

        info_embed = discord.Embed(
            title=f"{guild.name} — Server Information",
            description=(
                guild.description
                or "No server description has been set."
            ),
            colour=discord.Colour.blurple(),
            timestamp=discord.utils.utcnow(),
        )

        if guild.icon:
            info_embed.set_thumbnail(url=guild.icon.url)

        if guild.banner:
            info_embed.set_image(url=guild.banner.url)

        info_embed.add_field(
            name="General",
            value=(
                f"**Server ID:** `{guild.id}`\n"
                f"**Owner:** {owner_text}\n"
                f"**Created:** {created_at}\n"
                f"**Age:** {created_relative}"
            ),
            inline=False,
        )

        info_embed.add_field(
            name="Members",
            value=(
                f"**Total:** `{guild.member_count or 0:,}`\n"
                f"**Humans cached:** `{humans_cached:,}`\n"
                f"**Bots cached:** `{bots_cached:,}`"
            ),
            inline=True,
        )

        info_embed.add_field(
            name="Boosting",
            value=(
                f"**Boost level:** `{guild.premium_tier}`\n"
                f"**Boosts:** "
                f"`{guild.premium_subscription_count or 0}`"
            ),
            inline=True,
        )

        info_embed.add_field(
            name="Security",
            value=(
                f"**Verification:** `{verification_level}`\n"
                f"**2FA requirement:** "
                f"`{'Enabled' if guild.mfa_level else 'Disabled'}`\n"
                f"**Notifications:** `{notification_level}`"
            ),
            inline=True,
        )

        info_embed.add_field(
            name="Server counts",
            value=(
                f"**Roles:** `{len(guild.roles)}`\n"
                f"**Visible channels:** `{len(visible_channels)}`\n"
                f"**Categories:** `{len(categories)}`\n"
                f"**Text:** `{len(text_channels)}`\n"
                f"**Announcements:** `{len(announcement_channels)}`\n"
                f"**Forums:** `{len(forum_channels)}`\n"
                f"**Voice:** `{len(voice_channels)}`\n"
                f"**Stage:** `{len(stage_channels)}`\n"
                f"**Emojis:** `{len(guild.emojis)}`\n"
                f"**Stickers:** `{len(guild.stickers)}`"
            ),
            inline=False,
        )

        info_embed.add_field(
            name="Server features",
            value=self.limit_text(
                ", ".join(features) if features else "None"
            ),
            inline=False,
        )

        role_embed = discord.Embed(
            title=f"{guild.name} — Roles",
            description="All server roles:",
            colour=discord.Colour.orange(),
        )

        role_mentions = [
            role.mention
            for role in reversed(roles)
        ]

        role_chunks = self.comma_separated_chunks(
            role_mentions,
            limit=1024,
        )

        for index, role_chunk in enumerate(role_chunks):
            field_name = (
                "All roles"
                if index == 0
                else "All roles — continued"
            )

            role_embed.add_field(
                name=field_name,
                value=role_chunk,
                inline=False,
            )

        sticker_view = StickerView(guild)

        await ctx.send(
            embeds=[info_embed, role_embed],
            view=sticker_view,
            allowed_mentions=discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )


class StickerView(discord.ui.View):
    """View containing the custom-sticker button."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.guild = guild

    @discord.ui.button(
        label="Show custom stickers",
        style=discord.ButtonStyle.primary,
        emoji="🖼️",
    )
    async def show_stickers(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        stickers = self.guild.stickers

        if not stickers:
            await interaction.response.send_message(
                "This server has no custom stickers.",
                ephemeral=True,
            )
            return

        sticker_lines = [
            f"{sticker} `{sticker.name}`"
            for sticker in stickers
        ]

        sticker_text = ServerInfo.limit_text(
            "\n".join(sticker_lines),
            limit=4000,
        )

        sticker_embed = discord.Embed(
            title=f"{self.guild.name} — Custom Stickers",
            description=sticker_text,
            colour=discord.Colour.purple(),
        )

        await interaction.response.send_message(
            embed=sticker_embed,
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerInfo(bot))