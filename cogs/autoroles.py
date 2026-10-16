from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore, owner_or_has_guild_permissions
from database import connect_sqlite
from error_handling import handle_interaction_error, log_error

verify_group = app_commands.Group(name="verify", description="Configure member verification")
verified_role_group = app_commands.Group(name="verified-role", description="Manage verified roles", parent=verify_group)
unverified_role_group = app_commands.Group(name="unverified-role", description="Manage unverified roles", parent=verify_group)


class VerifyView(discord.ui.View):
	def __init__(self, cog: Autoroles):
		super().__init__(timeout=None)
		self.cog = cog

	@discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="verification:verify")
	async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
		await self.cog.verify_member(interaction)

	async def on_error(self, interaction, error, item):
		await handle_interaction_error(interaction, error, source="verification button")


class Autoroles(commands.GroupCog, group_name="autorole", group_description="Configure join autoroles"):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._verify_locks = {}
		self.store = SetupConfigStore(DB_PATH)
		with connect_sqlite(DB_PATH) as db:
			db.execute("CREATE TABLE IF NOT EXISTS guild_autorole_config (guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, bot_enabled INTEGER NOT NULL DEFAULT 0)")
			db.execute("CREATE TABLE IF NOT EXISTS guild_autoroles (guild_id INTEGER NOT NULL, role_id INTEGER NOT NULL, is_bot INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, role_id, is_bot))")
			db.execute("CREATE TABLE IF NOT EXISTS guild_verification_config (guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, channel_id INTEGER, message TEXT NOT NULL DEFAULT 'Click Verify to access the server.', minimum_account_days INTEGER NOT NULL DEFAULT 0)")
			db.execute("CREATE TABLE IF NOT EXISTS guild_verified_roles (guild_id INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY (guild_id, role_id))")
			db.execute("CREATE TABLE IF NOT EXISTS guild_unverified_roles (guild_id INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY (guild_id, role_id))")

		with connect_sqlite(DB_PATH) as db:
			db.execute("CREATE TABLE IF NOT EXISTS verification_attempts (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, attempted_at REAL NOT NULL, outcome TEXT NOT NULL, PRIMARY KEY(guild_id, user_id))")

	def _number(self, guild_id, key, default, maximum):
		try:
			return max(0, min(maximum, int(self.store.get(guild_id, "verification", key, default))))
		except (TypeError, ValueError):
			return default

	def validation_errors(self, guild):
		verified, unverified = self._roles(guild.id)
		errors = []
		if not verified:
			errors.append("Configure at least one verified role.")
		if set(verified) & set(unverified):
			errors.append("Verified and unverified roles must be different.")
		if not guild.me or not guild.me.guild_permissions.manage_roles:
			errors.append("Observer needs Manage Roles.")
		for role_id in set(verified + unverified):
			role = guild.get_role(role_id)
			if role is None or role.is_default() or role.managed or not guild.me or role >= guild.me.top_role:
				errors.append(f"Role {role_id} is missing or cannot be assigned; place Observer above it.")
		return errors

	async def verify_member(self, interaction):
		guild = interaction.guild
		if guild is None:
			await interaction.response.send_message("Verification only works in a server.", ephemeral=True)
			return
		await interaction.response.defer(ephemeral=True)
		key = guild.id
		lock = self._verify_locks.setdefault(key, asyncio.Lock())
		async with lock:
			await self._verify_locked(interaction)

	async def _verify_locked(self, interaction):
		guild = interaction.guild
		member = guild.get_member(interaction.user.id)
		if member is None or member.bot:
			await interaction.followup.send("Member unavailable for verification.", ephemeral=True)
			return
		config = self._config(guild.id)
		if not config["enabled"]:
			await interaction.followup.send("Verification is not enabled.", ephemeral=True)
			return
		now = time.time()
		with connect_sqlite(DB_PATH) as db:
			db.execute("DELETE FROM verification_attempts WHERE attempted_at < ?", (now - 30 * 86400,))
			previous = db.execute("SELECT attempted_at FROM verification_attempts WHERE guild_id=? AND user_id=?", (guild.id, member.id)).fetchone()
		cooldown = self._number(guild.id, "attempt_cooldown_seconds", 30, 3600)
		if previous and now - previous["attempted_at"] < cooldown:
			await interaction.followup.send("Please wait before trying verification again.", ephemeral=True)
			return
		errors = self.validation_errors(guild)
		verified, unverified = self._roles(guild.id)
		held = float(self.store.get(guild.id, "anti_raid", "hold_until", 0) or 0) > now
		minimum = self._number(guild.id, "minimum_account_days", 0, 3650)
		if held:
			outcome = "Verification is temporarily paused during a raid. Please try again later."
		elif errors:
			outcome = "Verification needs staff attention: " + " ".join(errors)
		elif discord.utils.utcnow() - member.created_at < timedelta(days=minimum):
			outcome = f"Your Discord account must be at least {minimum} days old to verify."
		elif unverified and not any(r.id in unverified for r in member.roles):
			outcome = "You do not have an unverified role; ask staff if you need access."
		else:
			# Grant access first: a failed add must never remove the holding role.
			try:
				await member.add_roles(*(guild.get_role(r) for r in verified), reason="Verification")
				await member.remove_roles(*(guild.get_role(r) for r in unverified), reason="Verification")
				outcome = "You have successfully verified."
			except discord.HTTPException as error:
				error_id = log_error(error, source="verification role assignment", guild_id=guild.id, user_id=member.id)
				outcome = f"Verification could not finish; ask staff to check your roles. Error ID: {error_id}"
		with connect_sqlite(DB_PATH) as db:
			db.execute("INSERT OR REPLACE INTO verification_attempts VALUES (?, ?, ?, ?)", (guild.id, member.id, now, outcome))
		await interaction.followup.send(outcome, ephemeral=True)
		channel_id = self.store.get(guild.id, "verification", "log_channel")
		channel = guild.get_channel(int(channel_id)) if channel_id else None
		if isinstance(channel, discord.TextChannel):
			await channel.send(f"Verification: <@{member.id}> (`{member.id}`) — {outcome}", allowed_mentions=discord.AllowedMentions.none())

	@verify_group.command(name="check", description="Check verification roles and bot permissions.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def verify_check(self, interaction: discord.Interaction):
		errors = self.validation_errors(interaction.guild)
		await interaction.response.send_message("\n".join(errors) or "Verification roles and permissions are ready.", ephemeral=True)

	def _config(self, guild_id: int):
		with connect_sqlite(DB_PATH) as db:
			row = db.execute("SELECT enabled, minimum_account_days FROM guild_verification_config WHERE guild_id = ?", (guild_id,)).fetchone()
		return {
			"enabled": bool(self.store.get(guild_id, "verification", "enabled", row["enabled"] if row else False)),
			"minimum_account_days": int(self.store.get(guild_id, "verification", "minimum_account_days", row["minimum_account_days"] if row else 0) or 0),
		}

	def _set_role(self, guild_id: int, table: str, role_id: int, add: bool) -> None:
		with connect_sqlite(DB_PATH) as db:
			if add:
				db.execute(f"INSERT OR IGNORE INTO {table} (guild_id, role_id) VALUES (?, ?)", (guild_id, role_id))
			else:
				db.execute(f"DELETE FROM {table} WHERE guild_id = ? AND role_id = ?", (guild_id, role_id))

	def _roles(self, guild_id: int):
		with connect_sqlite(DB_PATH) as db:
			verified = db.execute("SELECT role_id FROM guild_verified_roles WHERE guild_id = ?", (guild_id,)).fetchall()
			unverified = db.execute("SELECT role_id FROM guild_unverified_roles WHERE guild_id = ?", (guild_id,)).fetchall()
		verified_ids = {row["role_id"] for row in verified}
		unverified_ids = {row["role_id"] for row in unverified}
		for index in range(1, 6):
			verified_id = self.store.get(guild_id, "verification", f"verified_role_{index}")
			unverified_id = self.store.get(guild_id, "verification", f"unverified_role_{index}")
			if verified_id is not None:
				verified_ids.add(int(verified_id))
			if unverified_id is not None:
				unverified_ids.add(int(unverified_id))
		return list(verified_ids), list(unverified_ids)

	def _autorole_ids(self, guild_id: int, is_bot: bool, stored_rows) -> set[int]:
		role_ids = {row["role_id"] for row in stored_rows}
		prefix = "bot_role_" if is_bot else "member_role_"
		for index in range(1, 6):
			role_id = self.store.get(guild_id, "autorole", f"{prefix}{index}")
			if role_id is not None:
				role_ids.add(int(role_id))
		return role_ids

	@app_commands.command(name="enable", description="Enable join autoroles.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def enable(self, interaction: discord.Interaction):
		with connect_sqlite(DB_PATH) as db: db.execute("INSERT INTO guild_autorole_config (guild_id, enabled) VALUES (?, 1) ON CONFLICT(guild_id) DO UPDATE SET enabled=1", (interaction.guild.id,))
		await interaction.response.send_message("Join autoroles enabled.", ephemeral=True)

	@app_commands.command(name="disable", description="Disable join autoroles.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def disable(self, interaction: discord.Interaction):
		with connect_sqlite(DB_PATH) as db: db.execute("INSERT INTO guild_autorole_config (guild_id, enabled) VALUES (?, 0) ON CONFLICT(guild_id) DO UPDATE SET enabled=0", (interaction.guild.id,))
		await interaction.response.send_message("Join autoroles disabled.", ephemeral=True)

	@app_commands.command(name="add", description="Add a role given on join.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def add(self, interaction: discord.Interaction, role: discord.Role, bots: bool = False):
		with connect_sqlite(DB_PATH) as db: db.execute("INSERT OR IGNORE INTO guild_autoroles VALUES (?, ?, ?)", (interaction.guild.id, role.id, int(bots)))
		await interaction.response.send_message(f"Added {role.mention} to {'bot' if bots else 'member'} autoroles.", ephemeral=True)

	@app_commands.command(name="remove", description="Remove a join autorole.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def remove(self, interaction: discord.Interaction, role: discord.Role, bots: bool = False):
		with connect_sqlite(DB_PATH) as db: db.execute("DELETE FROM guild_autoroles WHERE guild_id = ? AND role_id = ? AND is_bot = ?", (interaction.guild.id, role.id, int(bots)))
		await interaction.response.send_message("Autorole removed.", ephemeral=True)

	@app_commands.command(name="list", description="List join autoroles.")
	@app_commands.guild_only()
	async def list(self, interaction: discord.Interaction):
		with connect_sqlite(DB_PATH) as db: rows = db.execute("SELECT role_id, is_bot FROM guild_autoroles WHERE guild_id = ?", (interaction.guild.id,)).fetchall()
		await interaction.response.send_message("\n".join(f"<@&{row['role_id']}> ({'bots' if row['is_bot'] else 'members'})" for row in rows) or "No autoroles configured.", ephemeral=True)

	@commands.Cog.listener()
	async def on_member_join(self, member: discord.Member):
		with connect_sqlite(DB_PATH) as db:
			config = db.execute("SELECT enabled, bot_enabled FROM guild_autorole_config WHERE guild_id = ?", (member.guild.id,)).fetchone()
			roles = db.execute("SELECT role_id FROM guild_autoroles WHERE guild_id = ? AND is_bot = ?", (member.guild.id, int(member.bot))).fetchall()
			_, unverified = self._roles(member.guild.id)
			verification = self._config(member.guild.id)
		autorole_enabled = bool(self.store.get(member.guild.id, "autorole", "bot_enabled" if member.bot else "enabled", config["bot_enabled"] if config else False))
		if autorole_enabled:
			role_ids = self._autorole_ids(member.guild.id, member.bot, roles)
			if verification["enabled"] and not member.bot:
				verified, _ = self._roles(member.guild.id)
				role_ids.difference_update(verified)
			await member.add_roles(*(role for role in (member.guild.get_role(role_id) for role_id in role_ids) if role), reason="Join autorole")
		if verification["enabled"] and not member.bot:
			await member.add_roles(*(role for role in (member.guild.get_role(role_id) for role_id in unverified) if role), reason="Unverified role")

	@app_commands.command(name="setup", description="Post or update the verification button.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def verify_setup(self, interaction: discord.Interaction):
		errors = self.validation_errors(interaction.guild)
		if errors:
			await interaction.response.send_message("\n".join(errors), ephemeral=True)
			return
		channel_id = self.store.get(interaction.guild.id, "verification", "channel")
		channel = interaction.guild.get_channel(int(channel_id)) if channel_id else interaction.channel
		if not isinstance(channel, discord.TextChannel):
			await interaction.response.send_message("Choose an available text channel in /setup first.", ephemeral=True)
			return
		await interaction.response.defer(ephemeral=True)
		message = self.store.get(interaction.guild.id, "verification", "message", "Click Verify to access the server.")
		await channel.send(message or "Click Verify to access the server.", view=VerifyView(self), allowed_mentions=discord.AllowedMentions.none())
		with connect_sqlite(DB_PATH) as db:
			db.execute("INSERT INTO guild_verification_config (guild_id, enabled, channel_id) VALUES (?, 1, ?) ON CONFLICT(guild_id) DO UPDATE SET enabled=1, channel_id=excluded.channel_id", (interaction.guild.id, channel.id))
		self.store.set(interaction.guild.id, "verification", "enabled", True)
		await interaction.followup.send(f"Verification panel posted in {channel.mention}.", ephemeral=True)

	@verify_group.command(name="enable", description="Enable button verification.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def verify_enable(self, interaction: discord.Interaction):
		self.store.set(interaction.guild.id, "verification", "enabled", True)
		await interaction.response.send_message("Verification enabled.", ephemeral=True)

	@verify_group.command(name="disable", description="Disable button verification.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def verify_disable(self, interaction: discord.Interaction):
		self.store.set(interaction.guild.id, "verification", "enabled", False)
		await interaction.response.send_message("Verification disabled.", ephemeral=True)

	@verify_group.command(name="channel", description="Set the verification channel.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def verify_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
		self.store.set(interaction.guild.id, "verification", "channel", channel.id)
		await interaction.response.send_message(f"Verification channel set to {channel.mention}.", ephemeral=True)

	@verified_role_group.command(name="add", description="Add a verified role.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def verified_role_add(self, interaction: discord.Interaction, role: discord.Role):
		self._set_role(interaction.guild.id, "guild_verified_roles", role.id, True)
		await interaction.response.send_message("Verified role added.", ephemeral=True)

	@verified_role_group.command(name="remove", description="Remove a verified role.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def verified_role_remove(self, interaction: discord.Interaction, role: discord.Role):
		self._set_role(interaction.guild.id, "guild_verified_roles", role.id, False)
		await interaction.response.send_message("Verified role removed.", ephemeral=True)

	@unverified_role_group.command(name="add", description="Add an unverified role.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def unverified_role_add(self, interaction: discord.Interaction, role: discord.Role):
		self._set_role(interaction.guild.id, "guild_unverified_roles", role.id, True)
		await interaction.response.send_message("Unverified role added.", ephemeral=True)

	@unverified_role_group.command(name="remove", description="Remove an unverified role.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def unverified_role_remove(self, interaction: discord.Interaction, role: discord.Role):
		self._set_role(interaction.guild.id, "guild_unverified_roles", role.id, False)
		await interaction.response.send_message("Unverified role removed.", ephemeral=True)

	@verify_group.command(name="message", description="Set the verification message.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_guild=True)
	async def verify_message(self, interaction: discord.Interaction, message: str):
		self.store.set(interaction.guild.id, "verification", "message", message)
		await interaction.response.send_message("Verification message updated.", ephemeral=True)


async def setup(bot: commands.Bot):
	cog = Autoroles(bot)
	await bot.add_cog(cog)

	# Verification commands are declared on a standalone group, so bind
	# their callbacks to this cog instance before registering the group.
	for command in verify_group.walk_commands():
		if isinstance(command, app_commands.Command):
			command.binding = cog

	bot.tree.remove_command("verify")
	bot.tree.add_command(verify_group)
	bot.add_view(VerifyView(cog))