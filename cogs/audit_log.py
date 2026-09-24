from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore
from database import ModerationCaseStore

MODULE_KEY = "audit_log"
MAX_IGNORED_CHANNELS = 5
MAX_IGNORED_ROLES = 5
FIELD_LIMIT = 1024
AUDIT_ENTRY_MAX_AGE = 15

# Severity colors (see class docstring for the convention).
COLOR_RED = 0xED4245      # deletions, kicks, bans
COLOR_AMBER = 0xF5A623    # edits / updates
COLOR_GREEN = 0x57F287    # joins / creates
COLOR_GRAY = 0x99AAB5     # leaves
COLOR_PURPLE = 0x9B59B6   # permission escalation warnings

# Permissions that warrant a distinct "escalation" warning when newly granted.
DANGEROUS_PERMISSIONS = (
    "administrator",
    "manage_roles",
    "manage_guild",
    "manage_webhooks",
    "manage_channels",
    "ban_members",
    "kick_members",
)

logger = logging.getLogger("observer.audit_log")


def _truncate(text: str, limit: int = FIELD_LIMIT) -> str:
    text = text if text else "*(empty)*"
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class AuditLogCog(commands.Cog):
    """
    Passive server-event audit logging, separate from ModerationCog.

    Every listener funnels through `_build_audit_embed` for a consistent
    look: author = actor, colored by severity, timestamped, and footer'd
    with IDs for traceability. Color convention:
        red    - deletions / kicks / bans
        amber  - edits / updates
        green  - joins / creates
        gray   - leaves
        purple - permission escalation warnings
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.cases = ModerationCaseStore(DB_PATH)

    # ============================================================ Config helpers

    async def _is_enabled(self, guild: discord.Guild, category: str | None = None) -> bool:
        if not self.store.get(guild.id, MODULE_KEY, "enabled", default=False):
            return False
        if category is None:
            return True
        return bool(self.store.get(guild.id, MODULE_KEY, category, default=True))

    async def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id = self.store.get(guild.id, MODULE_KEY, "log_channel", default=None)
        try:
            channel_id = int(channel_id)
        except (TypeError, ValueError):
            return None

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        return channel

    def _ignored_channel_ids(self, guild_id: int) -> set[int]:
        ids: set[int] = set()
        for index in range(1, MAX_IGNORED_CHANNELS + 1):
            value = self.store.get(guild_id, MODULE_KEY, f"ignored_channel_{index}", None)
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
        return ids

    def _ignored_role_ids(self, guild_id: int) -> set[int]:
        ids: set[int] = set()
        for index in range(1, MAX_IGNORED_ROLES + 1):
            value = self.store.get(guild_id, MODULE_KEY, f"ignored_role_{index}", None)
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
        return ids

    def _ignored_user_ids(self, guild_id: int) -> set[int]:
        raw = self.store.get(guild_id, MODULE_KEY, "ignored_user_ids", default=None)
        if not raw:
            return set()
        ids: set[int] = set()
        for chunk in str(raw).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                ids.add(int(chunk))
            except ValueError:
                continue
        return ids

    def _is_ignored(
        self,
        guild_id: int,
        *,
        channel: discord.abc.GuildChannel | None = None,
        member: discord.Member | None = None,
        user_id: int | None = None,
    ) -> bool:
        if channel is not None and channel.id in self._ignored_channel_ids(guild_id):
            return True

        if user_id is not None and user_id in self._ignored_user_ids(guild_id):
            return True

        if member is not None:
            if member.id in self._ignored_user_ids(guild_id):
                return True
            ignored_roles = self._ignored_role_ids(guild_id)
            if ignored_roles and any(role.id in ignored_roles for role in getattr(member, "roles", [])):
                return True

        return False

    # ============================================================ Embed helper

    def _build_audit_embed(
        self,
        title: str,
        color: int,
        *,
        author: discord.abc.User | discord.Member | None = None,
        thumbnail_url: str | None = None,
        description: str | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            color=color,
            description=description,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Observer Audit Log")
        if author is not None:
            icon_url = getattr(author.display_avatar, "url", None) if author else None
            embed.set_author(name=str(author), icon_url=icon_url)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        return embed

    @staticmethod
    def _add_diff_fields(embed: discord.Embed, label: str, before, after) -> None:
        embed.add_field(name=f"Before {label}", value=_truncate(str(before)), inline=True)
        embed.add_field(name=f"After {label}", value=_truncate(str(after)), inline=True)
        # Keep the field pair together on one row.
        embed.add_field(name="\u200b", value="\u200b", inline=True)

    async def _send(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await self._get_log_channel(guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning(
                "Missing permission to send audit log embeds in guild %s",
                guild.id,
            )
        except discord.NotFound:
            logger.warning("Audit log channel no longer exists in guild %s", guild.id)
        except discord.HTTPException:
            logger.warning("Failed to send audit log embed in guild %s", guild.id)

    # ============================================================ Actor resolution

    async def _resolve_actor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        *,
        target_id: int | None = None,
        retries: int = 2,
        delay: float = 1.0,
    ) -> discord.abc.User | None:
        """Best-effort lookup of who performed an action with no payload actor."""
        if not guild.me or not guild.me.guild_permissions.view_audit_log:
            return None

        for attempt in range(retries + 1):
            if attempt:
                await asyncio.sleep(delay)
            try:
                async for entry in guild.audit_logs(limit=5, action=action):
                    entry_age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                    if entry_age > AUDIT_ENTRY_MAX_AGE:
                        continue
                    if target_id is not None and getattr(entry.target, "id", None) != target_id:
                        continue
                    return entry.user
            except discord.Forbidden:
                return None
            except discord.HTTPException:
                continue
        return None

    # ============================================================ Escalation detection

    @staticmethod
    def _gained_dangerous_permissions(before: discord.Permissions, after: discord.Permissions) -> list[str]:
        gained = []
        for perm in DANGEROUS_PERMISSIONS:
            if not getattr(before, perm, False) and getattr(after, perm, False):
                gained.append(perm)
        return gained

    # ============================================================ Messages

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not await self._is_enabled(guild, "log_message_edits"):
            return

        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        if self._is_ignored(guild.id, channel=channel):
            return

        cached = payload.cached_message
        after_content = payload.data.get("content")
        author_id = payload.data.get("author", {}).get("id")

        if cached is not None:
            if cached.author.bot:
                return
            if after_content is not None and cached.content == after_content:
                return  # embed-only update, no real content change
            author = cached.author
            before_text = cached.content or "*(no text content)*"
        else:
            author = guild.get_member(int(author_id)) if author_id else None
            if author is not None and author.bot:
                return
            before_text = "*(uncached — content unavailable)*"

        after_text = after_content if after_content is not None else "*(uncached — content unavailable)*"

        jump_url = f"https://discord.com/channels/{guild.id}/{payload.channel_id}/{payload.message_id}"
        embed = self._build_audit_embed(
            "✏️ Message Edited",
            COLOR_AMBER,
            author=author,
            description=f"[Jump to message]({jump_url}) in {channel.mention}",
        )
        self._add_diff_fields(embed, "content", before_text, after_text)
        embed.set_footer(text=f"Message ID: {payload.message_id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not await self._is_enabled(guild, "log_message_deletes"):
            return

        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        if self._is_ignored(guild.id, channel=channel):
            return

        cached = payload.cached_message
        author = None
        content = "*(uncached — content unavailable)*"
        if cached is not None:
            if cached.author.bot:
                return
            author = cached.author
            content = cached.content or "*(no text content)*"

        embed = self._build_audit_embed(
            "🗑️ Message Deleted",
            COLOR_RED,
            author=author,
            thumbnail_url=getattr(author.display_avatar, "url", None) if author else None,
            description=f"In {channel.mention}",
        )
        embed.add_field(name="Content", value=_truncate(content), inline=False)
        embed.set_footer(text=f"Message ID: {payload.message_id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not await self._is_enabled(guild, "log_message_deletes"):
            return

        channel = guild.get_channel(payload.channel_id)
        if isinstance(channel, (discord.TextChannel, discord.Thread)) and self._is_ignored(guild.id, channel=channel):
            return

        embed = self._build_audit_embed(
            "🧹 Bulk Message Delete",
            COLOR_RED,
            description=f"{len(payload.message_ids)} messages deleted in "
                        f"{channel.mention if channel else f'<#{payload.channel_id}>'}",
        )
        embed.set_footer(text=f"Channel ID: {payload.channel_id}")
        await self._send(guild, embed)

    # ============================================================ Roles

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        guild = role.guild
        if not await self._is_enabled(guild, "log_role_changes"):
            return
        actor = await self._resolve_actor(guild, discord.AuditLogAction.role_create, target_id=role.id)
        embed = self._build_audit_embed(
            "✨ Role Created",
            COLOR_GREEN,
            author=actor,
            thumbnail_url=guild.icon.url if guild.icon else None,
            description=f"{role.mention}",
        )
        embed.set_footer(text=f"Role ID: {role.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        guild = role.guild
        if not await self._is_enabled(guild, "log_role_changes"):
            return
        actor = await self._resolve_actor(guild, discord.AuditLogAction.role_delete, target_id=role.id)
        embed = self._build_audit_embed(
            "🗑️ Role Deleted",
            COLOR_RED,
            author=actor,
            thumbnail_url=guild.icon.url if guild.icon else None,
            description=f"**{role.name}**",
        )
        embed.set_footer(text=f"Role ID: {role.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        guild = after.guild
        if not await self._is_enabled(guild, "log_role_changes"):
            return
        if after.id in self._ignored_role_ids(guild.id):
            return

        changes: list[tuple[str, str, str]] = []
        if before.name != after.name:
            changes.append(("Name", before.name, after.name))
        if before.color != after.color:
            changes.append(("Color", str(before.color), str(after.color)))
        if before.hoist != after.hoist:
            changes.append(("Hoisted", str(before.hoist), str(after.hoist)))
        if before.mentionable != after.mentionable:
            changes.append(("Mentionable", str(before.mentionable), str(after.mentionable)))

        gained_perms = self._gained_dangerous_permissions(before.permissions, after.permissions)
        if before.permissions != after.permissions:
            changes.append(("Permissions", str(before.permissions.value), str(after.permissions.value)))

        if not changes:
            return

        actor = await self._resolve_actor(guild, discord.AuditLogAction.role_update, target_id=after.id)
        is_escalation = bool(gained_perms) and await self._is_enabled(guild, "log_permission_escalation")

        title = "⚠️ Permission Escalation: Role Updated" if is_escalation else "🔧 Role Updated"
        color = COLOR_PURPLE if is_escalation else COLOR_AMBER

        embed = self._build_audit_embed(
            title,
            color,
            author=actor,
            thumbnail_url=guild.icon.url if guild.icon else None,
            description=f"{after.mention}" + (
                f"\n**Gained:** {', '.join(gained_perms)}" if is_escalation else ""
            ),
        )
        for label, before_val, after_val in changes:
            self._add_diff_fields(embed, label, before_val, after_val)
        embed.set_footer(text=f"Role ID: {after.id}")
        await self._send(guild, embed)

    # ============================================================ Channels

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        guild = channel.guild
        if not await self._is_enabled(guild, "log_channel_changes"):
            return
        if self._is_ignored(guild.id, channel=channel):
            return
        actor = await self._resolve_actor(guild, discord.AuditLogAction.channel_create, target_id=channel.id)
        embed = self._build_audit_embed(
            "✨ Channel Created",
            COLOR_GREEN,
            author=actor,
            thumbnail_url=guild.icon.url if guild.icon else None,
            description=f"{channel.mention if hasattr(channel, 'mention') else channel.name}",
        )
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        guild = channel.guild
        if not await self._is_enabled(guild, "log_channel_changes"):
            return
        if self._is_ignored(guild.id, channel=channel):
            return
        actor = await self._resolve_actor(guild, discord.AuditLogAction.channel_delete, target_id=channel.id)
        embed = self._build_audit_embed(
            "🗑️ Channel Deleted",
            COLOR_RED,
            author=actor,
            thumbnail_url=guild.icon.url if guild.icon else None,
            description=f"**#{channel.name}**",
        )
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        guild = after.guild
        if not await self._is_enabled(guild, "log_channel_changes"):
            return
        if self._is_ignored(guild.id, channel=after):
            return

        changes: list[tuple[str, str, str]] = []
        if before.name != after.name:
            changes.append(("Name", before.name, after.name))
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append(("Topic", getattr(before, "topic", None) or "", getattr(after, "topic", None) or ""))
        if before.category != after.category:
            changes.append((
                "Category",
                before.category.name if before.category else "None",
                after.category.name if after.category else "None",
            ))
        if before.overwrites != after.overwrites:
            changes.append(("Permission overwrites", "(see audit log)", "(changed)"))

        if not changes:
            return

        actor = await self._resolve_actor(guild, discord.AuditLogAction.channel_update, target_id=after.id)
        embed = self._build_audit_embed(
            "🔧 Channel Updated",
            COLOR_AMBER,
            author=actor,
            thumbnail_url=guild.icon.url if guild.icon else None,
            description=f"{after.mention if hasattr(after, 'mention') else after.name}",
        )
        for label, before_val, after_val in changes:
            self._add_diff_fields(embed, label, before_val, after_val)
        embed.set_footer(text=f"Channel ID: {after.id}")
        await self._send(guild, embed)

    # ============================================================ Webhooks / emojis / stickers

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel) -> None:
        guild = channel.guild
        if not await self._is_enabled(guild, "log_webhook_changes"):
            return
        if self._is_ignored(guild.id, channel=channel):
            return

        embed = self._build_audit_embed(
            "🪝 Webhook Changed",
            COLOR_AMBER,
            thumbnail_url=guild.icon.url if guild.icon else None,
            description=f"In {channel.mention}",
        )
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_guild_emojis_update(
        self, guild: discord.Guild, before: list[discord.Emoji], after: list[discord.Emoji]
    ) -> None:
        if not await self._is_enabled(guild, "log_emoji_changes"):
            return

        before_ids = {emoji.id for emoji in before}
        after_ids = {emoji.id for emoji in after}
        added = [emoji for emoji in after if emoji.id not in before_ids]
        removed = [emoji for emoji in before if emoji.id not in after_ids]
        if not added and not removed:
            return

        description_parts = []
        if added:
            description_parts.append("Added: " + ", ".join(str(emoji) for emoji in added))
        if removed:
            description_parts.append("Removed: " + ", ".join(f":{emoji.name}:" for emoji in removed))

        embed = self._build_audit_embed(
            "😀 Emojis Updated",
            COLOR_AMBER if added and removed else (COLOR_GREEN if added else COLOR_RED),
            thumbnail_url=guild.icon.url if guild.icon else None,
            description="\n".join(description_parts),
        )
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_guild_stickers_update(
        self, guild: discord.Guild, before: list[discord.GuildSticker], after: list[discord.GuildSticker]
    ) -> None:
        if not await self._is_enabled(guild, "log_emoji_changes"):
            return

        before_ids = {sticker.id for sticker in before}
        after_ids = {sticker.id for sticker in after}
        added = [sticker for sticker in after if sticker.id not in before_ids]
        removed = [sticker for sticker in before if sticker.id not in after_ids]
        if not added and not removed:
            return

        description_parts = []
        if added:
            description_parts.append("Added: " + ", ".join(sticker.name for sticker in added))
        if removed:
            description_parts.append("Removed: " + ", ".join(sticker.name for sticker in removed))

        embed = self._build_audit_embed(
            "🏷️ Stickers Updated",
            COLOR_AMBER if added and removed else (COLOR_GREEN if added else COLOR_RED),
            thumbnail_url=guild.icon.url if guild.icon else None,
            description="\n".join(description_parts),
        )
        await self._send(guild, embed)

    # ============================================================ Members

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        self.cases.add_name(guild.id, member.id, member.name)
        self.cases.add_name(guild.id, member.id, member.display_name)
        if not await self._is_enabled(guild, "log_member_events"):
            return
        if self._is_ignored(guild.id, member=member):
            return
        embed = self._build_audit_embed(
            "📥 Member Joined",
            COLOR_GREEN,
            author=member,
            thumbnail_url=member.display_avatar.url,
            description=f"{member.mention} ({member})",
        )
        embed.set_footer(text=f"User ID: {member.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        if not await self._is_enabled(guild, "log_member_events"):
            return
        if self._is_ignored(guild.id, member=member):
            return

        # Distinguish a kick from a plain leave via the audit log.
        actor = await self._resolve_actor(guild, discord.AuditLogAction.kick, target_id=member.id, retries=1)
        if actor is not None:
            embed = self._build_audit_embed(
                "🥾 Member Kicked",
                COLOR_RED,
                author=actor,
                thumbnail_url=member.display_avatar.url,
                description=f"{member.mention} ({member}) was kicked",
            )
        else:
            embed = self._build_audit_embed(
                "📤 Member Left",
                COLOR_GRAY,
                author=member,
                thumbnail_url=member.display_avatar.url,
                description=f"{member.mention} ({member}) left",
            )
        embed.set_footer(text=f"User ID: {member.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        if not await self._is_enabled(guild, "log_member_events"):
            return
        if self._is_ignored(guild.id, user_id=user.id):
            return
        actor = await self._resolve_actor(guild, discord.AuditLogAction.ban, target_id=user.id)
        embed = self._build_audit_embed(
            "🔨 Member Banned",
            COLOR_RED,
            author=actor,
            thumbnail_url=user.display_avatar.url,
            description=f"{user.mention} ({user})",
        )
        embed.set_footer(text=f"User ID: {user.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        if not await self._is_enabled(guild, "log_member_events"):
            return
        if self._is_ignored(guild.id, user_id=user.id):
            return
        actor = await self._resolve_actor(guild, discord.AuditLogAction.unban, target_id=user.id)
        embed = self._build_audit_embed(
            "🔓 Member Unbanned",
            COLOR_GREEN,
            author=actor,
            thumbnail_url=user.display_avatar.url,
            description=f"{user.mention} ({user})",
        )
        embed.set_footer(text=f"User ID: {user.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild
        self.cases.add_name(guild.id, after.id, after.name)
        self.cases.add_name(guild.id, after.id, after.display_name)
        if not await self._is_enabled(guild, "log_member_events"):
            return
        if self._is_ignored(guild.id, member=after):
            return

        # Timeout change
        if before.timed_out_until != after.timed_out_until:
            actor = await self._resolve_actor(guild, discord.AuditLogAction.member_update, target_id=after.id)
            if after.timed_out_until and (
                before.timed_out_until is None or after.timed_out_until > discord.utils.utcnow()
            ):
                embed = self._build_audit_embed(
                    "⏱️ Member Timed Out",
                    COLOR_RED,
                    author=actor,
                    thumbnail_url=after.display_avatar.url,
                    description=f"{after.mention} until "
                                f"{discord.utils.format_dt(after.timed_out_until, style='F')}",
                )
            else:
                embed = self._build_audit_embed(
                    "⏱️ Timeout Removed",
                    COLOR_GREEN,
                    author=actor,
                    thumbnail_url=after.display_avatar.url,
                    description=f"{after.mention}",
                )
            embed.set_footer(text=f"User ID: {after.id}")
            await self._send(guild, embed)

        # Nickname change
        if before.nick != after.nick:
            embed = self._build_audit_embed(
                "✏️ Nickname Changed",
                COLOR_AMBER,
                author=after,
                thumbnail_url=after.display_avatar.url,
            )
            self._add_diff_fields(embed, "nickname", before.nick or before.name, after.nick or after.name)
            embed.set_footer(text=f"User ID: {after.id}")
            await self._send(guild, embed)

        # Role changes
        before_roles = set(before.roles)
        after_roles = set(after.roles)
        if before_roles != after_roles:
            added = after_roles - before_roles
            removed = before_roles - after_roles

            before_perms = discord.Permissions.none()
            for role in before_roles:
                before_perms |= role.permissions
            after_perms = discord.Permissions.none()
            for role in after_roles:
                after_perms |= role.permissions
            gained_perms = self._gained_dangerous_permissions(before_perms, after_perms)

            actor = await self._resolve_actor(guild, discord.AuditLogAction.member_role_update, target_id=after.id)
            is_escalation = bool(gained_perms) and await self._is_enabled(guild, "log_permission_escalation")

            title = "⚠️ Permission Escalation: Roles Changed" if is_escalation else "🔧 Member Roles Changed"
            color = COLOR_PURPLE if is_escalation else COLOR_AMBER

            description = f"{after.mention}"
            if is_escalation:
                description += f"\n**Gained:** {', '.join(gained_perms)}"

            embed = self._build_audit_embed(
                title,
                color,
                author=actor,
                thumbnail_url=after.display_avatar.url,
                description=description,
            )
            if added:
                embed.add_field(name="Roles Added", value=_truncate(", ".join(r.mention for r in added)), inline=False)
            if removed:
                embed.add_field(name="Roles Removed", value=_truncate(", ".join(r.mention for r in removed)), inline=False)
            embed.set_footer(text=f"User ID: {after.id}")
            await self._send(guild, embed)

    # ============================================================ Voice

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        guild = member.guild
        if before.channel == after.channel:
            return
        if not await self._is_enabled(guild, "log_voice_moves"):
            return
        if self._is_ignored(guild.id, member=member):
            return

        if before.channel is None and after.channel is not None:
            title, description, color = "🔊 Voice Join", f"{member.mention} joined {after.channel.mention}", COLOR_GREEN
        elif before.channel is not None and after.channel is None:
            title, description, color = "🔇 Voice Leave", f"{member.mention} left {before.channel.mention}", COLOR_GRAY
        else:
            title = "🔀 Voice Switch"
            description = f"{member.mention} moved from {before.channel.mention} to {after.channel.mention}"
            color = COLOR_AMBER

        embed = self._build_audit_embed(title, color, author=member, thumbnail_url=member.display_avatar.url, description=description)
        embed.set_footer(text=f"User ID: {member.id}")
        await self._send(guild, embed)

    # ============================================================ Invites

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if guild is None or not isinstance(guild, discord.Guild):
            return
        if not await self._is_enabled(guild, "log_invites"):
            return
        if invite.channel is not None and self._is_ignored(guild.id, channel=invite.channel):
            return

        embed = self._build_audit_embed(
            "🔗 Invite Created",
            COLOR_GREEN,
            author=invite.inviter,
            description=f"Code `{invite.code}` for {invite.channel.mention if invite.channel else 'unknown channel'}"
                        + (f"\nMax uses: {invite.max_uses}" if invite.max_uses else "")
                        + (f"\nExpires: {discord.utils.format_dt(invite.expires_at, style='R')}" if invite.expires_at else ""),
        )
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if guild is None or not isinstance(guild, discord.Guild):
            return
        if not await self._is_enabled(guild, "log_invites"):
            return

        embed = self._build_audit_embed(
            "🔗 Invite Deleted",
            COLOR_RED,
            description=f"Code `{invite.code}`",
        )
        await self._send(guild, embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AuditLogCog(bot))
