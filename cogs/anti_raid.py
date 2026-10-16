"""Opt-in join-burst protection and honeypot enforcement."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore, owner_or_has_guild_permissions
from database import ModerationCaseStore
from error_handling import ObserverCog, log_error


class AntiRaid(ObserverCog):
    def __init__(self, bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.cases = ModerationCaseStore(DB_PATH)
        self.joins = defaultdict(lambda: deque(maxlen=1000))
        self.locks = defaultdict(asyncio.Lock)
        self.trapped = set()

    def get(self, guild_id, key, default=None):
        return self.store.get(guild_id, "anti_raid", key, default)

    def number(self, guild_id, key, default, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(self.get(guild_id, key, default))))
        except (ValueError, TypeError):
            return default

    def exempt(self, member):
        bypass = self.get(member.guild.id, "bypass_role")
        return (member.bot or member.id == member.guild.owner_id
                or member.guild_permissions.administrator
                or member.guild_permissions.manage_guild
                or member.guild_permissions.moderate_members
                or member.guild_permissions.ban_members
                or member.guild_permissions.manage_messages
                or member.guild_permissions.kick_members
                or any(str(r.id) == str(bypass) for r in member.roles))

    async def alert(self, guild, text):
        channel_id = self.get(guild.id, "alert_channel")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as error:
                log_error(error, source="anti-raid alert", guild_id=guild.id)

    async def restrict(self, member, reason, *, ban=False, minutes=10):
        me = member.guild.me
        if self.exempt(member):
            return "exempt"
        permission = "ban_members" if ban else "moderate_members"
        if not me or not getattr(me.guild_permissions, permission) or member.top_role >= me.top_role:
            return f"not applied: missing {permission} or role hierarchy"
        try:
            if ban:
                await member.ban(reason=reason, delete_message_seconds=0)
            elif minutes:
                deadline = discord.utils.utcnow() + timedelta(minutes=minutes)
                existing = getattr(member, "timed_out_until", None)
                if existing is not None and existing >= deadline:
                    return "existing longer timeout kept"
                await member.timeout(deadline, reason=reason)
            else:
                return "timeout disabled"
        except discord.HTTPException as error:
            error_id = log_error(error, source="anti-raid enforcement", guild_id=member.guild.id, user_id=member.id)
            return f"failed; error {error_id}"
        self.cases.create_case(member.guild.id, "ban" if ban else "timeout", member.id,
                               self.bot.user.id, reason, status="resolved")
        return "banned" if ban else f"timed out for {minutes} minutes"

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not self.get(member.guild.id, "enabled", False) or self.exempt(member):
            return
        async with self.locks[member.guild.id]:
            now = time.time()
            joins = self.joins[member.guild.id]
            window = self.number(member.guild.id, "join_window_seconds", 30, 5, 3600)
            while joins and now - joins[0][0] > window:
                joins.popleft()
            joins.append((now, member.id))
            threshold = self.number(member.guild.id, "join_threshold", 10, 2, 1000)
            active = float(self.get(member.guild.id, "hold_until", 0) or 0) > now
            triggered = not active and len(joins) >= threshold
            if triggered:
                hold = self.number(member.guild.id, "hold_seconds", 300, 10, 86400)
                # Persist before awaiting Discord: verification sees the hold immediately.
                self.store.set(member.guild.id, "anti_raid", "hold_until", now + hold)
            if not active and not triggered:
                return
            ids = [uid for _, uid in joins] if triggered else [member.id]
            minutes = self.number(member.guild.id, "timeout_minutes", 10, 0, 40320)
            results = []
            for uid in ids:
                target = member.guild.get_member(uid)
                if target:
                    results.append(f"{uid}: {await self.restrict(target, 'Automatic join-burst anti-raid restriction', minutes=minutes)}")
            await self.alert(member.guild, "**Anti-raid verification hold active**\n"
                             + (f"Detected {len(joins)} joins in {window}s.\n" if triggered else "New arrival during hold.\n")
                             + "\n".join(results))

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.webhook_id or not isinstance(message.author, discord.Member):
            return
        guild = message.guild
        if (not self.get(guild.id, "honeypot_enabled", False)
                or str(message.channel.id) != str(self.get(guild.id, "honeypot_channel"))
                or self.exempt(message.author)):
            return
        key = (guild.id, message.author.id)
        if key in self.trapped:
            return
        self.trapped.add(key)
        try:
            result = await self.restrict(message.author, "Posted in the configured do-not-post honeypot channel",
                                         ban=bool(self.get(guild.id, "honeypot_ban", False)), minutes=60)
            try:
                await message.delete()
            except discord.HTTPException as error:
                log_error(error, source="honeypot delete", guild_id=guild.id, user_id=message.author.id)
            await self.alert(guild, f"**Honeypot triggered**\nMember: {message.author.id}\nChannel: {message.channel.id}\nAction: {result}")
        finally:
            self.trapped.discard(key)

    @app_commands.command(name="raid-status", description="Show raid hold and honeypot status, or release the hold.")
    @app_commands.guild_only()
    @owner_or_has_guild_permissions(manage_guild=True)
    async def raid_status(self, interaction: discord.Interaction, release: bool = False):
        if release:
            await interaction.response.defer(ephemeral=True)
            async with self.locks[interaction.guild.id]:
                self.store.set(interaction.guild.id, "anti_raid", "hold_until", 0)
                self.joins.pop(interaction.guild.id, None)
        remaining = max(0, int(float(self.get(interaction.guild.id, "hold_until", 0) or 0) - time.time()))
        send = interaction.followup.send if release else interaction.response.send_message
        await send(
            f"Verification hold: {remaining}s remaining.\nAutomatic anti-raid: {bool(self.get(interaction.guild.id, 'enabled', False))}.\n"
            f"Honeypot: {bool(self.get(interaction.guild.id, 'honeypot_enabled', False))}.\n"
            "Releasing the hold does not clear member timeouts. Configure protection in /setup.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AntiRaid(bot))
