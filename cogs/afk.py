from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore
from database import connect_sqlite


class AFK(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.store = SetupConfigStore(DB_PATH)
		with connect_sqlite(DB_PATH) as db:
			db.execute("""CREATE TABLE IF NOT EXISTS afk_users (
				guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, reason TEXT NOT NULL,
				started_at REAL NOT NULL, original_nick TEXT, PRIMARY KEY (guild_id, user_id))""")

	@app_commands.command(name="afk", description="Mark yourself as AFK in this server.")
	@app_commands.describe(reason="Why you are AFK")
	@app_commands.guild_only()
	async def afk(self, interaction: discord.Interaction, reason: str = "AFK"):
		member = interaction.user
		now = time.time()
		original_nick = member.nick if isinstance(member, discord.Member) else None
		with connect_sqlite(DB_PATH) as db:
			old = db.execute("SELECT original_nick FROM afk_users WHERE guild_id = ? AND user_id = ?", (interaction.guild.id, member.id)).fetchone()
			db.execute("INSERT INTO afk_users (guild_id, user_id, reason, started_at, original_nick) VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET reason=excluded.reason, started_at=excluded.started_at", (interaction.guild.id, member.id, reason.strip() or "AFK", now, old["original_nick"] if old else original_nick))
		prefix = str(self.store.get(interaction.guild.id, "afk", "nickname_prefix", "[AFK]") or "").strip()
		if prefix and isinstance(member, discord.Member):
			try:
				await member.edit(nick=f"{prefix} {member.display_name}"[:32], reason="AFK status")
			except discord.HTTPException:
				pass
		await interaction.response.send_message(f"You are now AFK: {reason.strip() or 'AFK'}", ephemeral=True)

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message):
		if message.guild is None or message.author.bot or message.webhook_id:
			return
		if message.content.startswith(("!", "/")):
			return
		ignored = self.store.get(message.guild.id, "afk", "ignored_channels", "") or ""
		if str(message.channel.id) in {part.strip() for part in str(ignored).split(",") if part.strip()}:
			return
		with connect_sqlite(DB_PATH) as db:
			own = db.execute("SELECT * FROM afk_users WHERE guild_id = ? AND user_id = ?", (message.guild.id, message.author.id)).fetchone()
			mentioned_rows = [db.execute("SELECT * FROM afk_users WHERE guild_id = ? AND user_id = ?", (message.guild.id, uid)).fetchone() for uid in {member.id for member in message.mentions}]
		if own:
			duration = int((time.time() - own["started_at"]) // 60)
			with connect_sqlite(DB_PATH) as db:
				db.execute("DELETE FROM afk_users WHERE guild_id = ? AND user_id = ?", (message.guild.id, message.author.id))
			if isinstance(message.author, discord.Member):
				try:
					await message.author.edit(nick=own["original_nick"], reason="Returned from AFK")
				except discord.HTTPException:
					pass
			await message.channel.send(f"Welcome back {message.author.mention}. I removed your AFK status.\nYou were AFK for {duration} minutes.")
		seen = set()
		for row in mentioned_rows:
			if row and row["user_id"] not in seen:
				seen.add(row["user_id"])
				duration = int((time.time() - row["started_at"]) // 60)
				member = message.guild.get_member(row["user_id"])
				await message.channel.send(f"{member.display_name if member else 'That user'} is currently AFK.\nReason: {row['reason']}\nAFK for: {duration} minutes")


async def setup(bot: commands.Bot):
	await bot.add_cog(AFK(bot))