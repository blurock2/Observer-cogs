from __future__ import annotations

import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, owner_or_has_guild_permissions
from database import connect_sqlite


def _duration(value: str) -> int | None:
	units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
	try:
		return int(float(value[:-1]) * units[value[-1].lower()])
	except (KeyError, ValueError, TypeError):
		return None


class TempRoles(commands.GroupCog, group_name="temprole", group_description="Manage temporary roles"):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.task: asyncio.Task | None = None
		with connect_sqlite(DB_PATH) as db:
			db.execute("""CREATE TABLE IF NOT EXISTS temporary_roles (
				guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
				staff_id INTEGER NOT NULL, reason TEXT, assigned_at REAL NOT NULL,
				expires_at REAL NOT NULL, PRIMARY KEY (guild_id, user_id, role_id))""")

	async def cog_load(self):
		self.task = asyncio.create_task(self._expiry_loop())

	def cog_unload(self):
		if self.task:
			self.task.cancel()

	async def _expiry_loop(self):
		while True:
			with connect_sqlite(DB_PATH) as db:
				rows = db.execute("SELECT * FROM temporary_roles WHERE expires_at <= ?", (time.time(),)).fetchall()
			for row in rows:
				guild = self.bot.get_guild(row["guild_id"])
				member = guild.get_member(row["user_id"]) if guild else None
				role = guild.get_role(row["role_id"]) if guild else None
				if member and role and role in member.roles:
					try:
						await member.remove_roles(role, reason="Temporary role expired")
					except discord.HTTPException:
						continue
				with connect_sqlite(DB_PATH) as db:
					db.execute("DELETE FROM temporary_roles WHERE guild_id = ? AND user_id = ? AND role_id = ?", (row["guild_id"], row["user_id"], row["role_id"]))
			await asyncio.sleep(30)

	def _can_manage(self, interaction: discord.Interaction, role: discord.Role) -> bool:
		member = interaction.guild.me
		return bool(member and member.guild_permissions.manage_roles and not role.managed and role < member.top_role)

	@app_commands.command(name="add", description="Give a member a temporary role.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_roles=True)
	async def add(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role, duration: str, reason: str | None = None):
		seconds = _duration(duration)
		if seconds is None or seconds <= 0:
			await interaction.response.send_message("Duration must look like `30m`, `2h`, or `7d`.", ephemeral=True)
			return
		if not self._can_manage(interaction, role):
			await interaction.response.send_message("I cannot manage that role. Check Manage Roles and role hierarchy.", ephemeral=True)
			return
		await user.add_roles(role, reason=reason or f"Temporary role by {interaction.user}")
		with connect_sqlite(DB_PATH) as db:
			db.execute("INSERT INTO temporary_roles VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id, user_id, role_id) DO UPDATE SET staff_id=excluded.staff_id, reason=excluded.reason, assigned_at=excluded.assigned_at, expires_at=excluded.expires_at", (interaction.guild.id, user.id, role.id, interaction.user.id, reason, time.time(), time.time() + seconds))
		await interaction.response.send_message(f"Gave {role.mention} to {user.mention} for {duration}.", ephemeral=True)

	@app_commands.command(name="remove", description="Remove a temporary role.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_roles=True)
	async def remove(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
		if not self._can_manage(interaction, role):
			await interaction.response.send_message("I cannot manage that role.", ephemeral=True)
			return
		await user.remove_roles(role, reason=f"Temporary role removed by {interaction.user}")
		with connect_sqlite(DB_PATH) as db:
			db.execute("DELETE FROM temporary_roles WHERE guild_id = ? AND user_id = ? AND role_id = ?", (interaction.guild.id, user.id, role.id))
		await interaction.response.send_message("Temporary role removed.", ephemeral=True)

	@app_commands.command(name="list", description="List a member's temporary roles.")
	@app_commands.guild_only()
	async def list(self, interaction: discord.Interaction, user: discord.Member):
		with connect_sqlite(DB_PATH) as db:
			rows = db.execute("SELECT * FROM temporary_roles WHERE guild_id = ? AND user_id = ? ORDER BY expires_at", (interaction.guild.id, user.id)).fetchall()
		text = "\n".join(f"<@&{row['role_id']}> expires <t:{int(row['expires_at'])}:R>" for row in rows) or "No temporary roles."
		await interaction.response.send_message(f"Temporary Roles for {user.mention}\n{text}", ephemeral=True)

	@app_commands.command(name="server-list", description="List all temporary roles in this server.")
	@app_commands.guild_only()
	@owner_or_has_guild_permissions(manage_roles=True)
	async def server_list(self, interaction: discord.Interaction):
		with connect_sqlite(DB_PATH) as db:
			rows = db.execute("SELECT * FROM temporary_roles WHERE guild_id = ? ORDER BY expires_at LIMIT 25", (interaction.guild.id,)).fetchall()
		text = "\n".join(f"<@{row['user_id']}> <@&{row['role_id']}> expires <t:{int(row['expires_at'])}:R>" for row in rows) or "No temporary roles."
		await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot):
	await bot.add_cog(TempRoles(bot))