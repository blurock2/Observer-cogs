from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH
from database import connect_sqlite

bookmark_group = app_commands.Group(name="bookmark", description="Manage your private bookmarks")


class BookmarkView(discord.ui.View):
	def __init__(self, cog: Bookmarks, user_id: int, bookmark_id: int):
		super().__init__(timeout=300)
		self.cog, self.user_id, self.bookmark_id = cog, user_id, bookmark_id
		jump = discord.ui.Button(label="Jump to Message", style=discord.ButtonStyle.link, url=cog._url(bookmark_id))
		delete = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)
		delete.callback = self._delete
		self.add_item(jump)
		self.add_item(delete)

	async def _delete(self, interaction: discord.Interaction):
		with connect_sqlite(DB_PATH) as db:
			result = db.execute("DELETE FROM bookmarks WHERE id = ? AND user_id = ?", (self.bookmark_id, self.user_id))
		await interaction.response.send_message("Bookmark deleted." if result.rowcount else "Bookmark not found.", ephemeral=True)


class Bookmarks(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		with connect_sqlite(DB_PATH) as db:
			db.execute("""CREATE TABLE IF NOT EXISTS bookmarks (
				id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
				guild_id INTEGER, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
				author_id INTEGER NOT NULL, content TEXT NOT NULL, attachment_urls TEXT NOT NULL,
				message_url TEXT NOT NULL, bookmarked_at REAL NOT NULL, original_timestamp REAL)""")

	def _url(self, bookmark_id: int) -> str:
		with connect_sqlite(DB_PATH) as db:
			row = db.execute("SELECT message_url FROM bookmarks WHERE id = ?", (bookmark_id,)).fetchone()
		return row["message_url"] if row else "https://discord.com"

	def _embed(self, row) -> discord.Embed:
		embed = discord.Embed(title=f"Bookmark #{row['id']}", description=f'"{row["content"] or "(no text)"}"', color=0x96EDF1)
		embed.add_field(name="Author", value=f"<@{row['author_id']}>\nChannel: <#{row['channel_id']}>" , inline=False)
		embed.add_field(name="Saved", value=f"<t:{int(row['bookmarked_at'])}:R>", inline=False)
		if row["attachment_urls"]:
			embed.add_field(name="Attachments", value=row["attachment_urls"], inline=False)
		return embed

	async def bookmark_message(self, interaction: discord.Interaction, message: discord.Message):
		attachments = "\n".join(attachment.url for attachment in message.attachments)
		with connect_sqlite(DB_PATH) as db:
			cursor = db.execute("INSERT INTO bookmarks (user_id, guild_id, channel_id, message_id, author_id, content, attachment_urls, message_url, bookmarked_at, original_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (interaction.user.id, message.guild.id if message.guild else None, message.channel.id, message.id, message.author.id, message.content, attachments, message.jump_url, time.time(), message.created_at.timestamp()))
			bookmark_id = cursor.lastrowid
		await interaction.response.send_message("Message bookmarked.", ephemeral=True, view=BookmarkView(self, interaction.user.id, bookmark_id))

	@app_commands.command(name="bookmarks", description="View your private bookmarks.")
	async def bookmarks(self, interaction: discord.Interaction, search: str | None = None):
		query = "SELECT * FROM bookmarks WHERE user_id = ?"
		params: list[object] = [interaction.user.id]
		if search:
			query += " AND content LIKE ?"
			params.append(f"%{search}%")
		query += " ORDER BY bookmarked_at DESC LIMIT 10"
		with connect_sqlite(DB_PATH) as db:
			rows = db.execute(query, params).fetchall()
		if not rows:
			await interaction.response.send_message("You have no bookmarks.", ephemeral=True)
			return
		await interaction.response.send_message(embed=self._embed(rows[0]), view=BookmarkView(self, interaction.user.id, rows[0]["id"]), ephemeral=True)

	@app_commands.command(name="bookmark-delete", description="Delete one of your bookmarks.")
	async def bookmark_delete(self, interaction: discord.Interaction, id: int):
		with connect_sqlite(DB_PATH) as db:
			result = db.execute("DELETE FROM bookmarks WHERE id = ? AND user_id = ?", (id, interaction.user.id))
		await interaction.response.send_message("Bookmark deleted." if result.rowcount else "Bookmark not found.", ephemeral=True)

	@app_commands.command(name="bookmark-clear", description="Delete all of your bookmarks.")
	async def bookmark_clear(self, interaction: discord.Interaction):
		with connect_sqlite(DB_PATH) as db:
			db.execute("DELETE FROM bookmarks WHERE user_id = ?", (interaction.user.id,))
		await interaction.response.send_message("Bookmarks cleared.", ephemeral=True)

	@bookmark_group.command(name="delete", description="Delete one of your bookmarks.")
	async def bookmark_delete_subcommand(self, interaction: discord.Interaction, id: int):
		await self.bookmark_delete.callback(self, interaction, id)

	@bookmark_group.command(name="clear", description="Delete all of your bookmarks.")
	async def bookmark_clear_subcommand(self, interaction: discord.Interaction):
		await self.bookmark_clear.callback(self, interaction)


async def setup(bot: commands.Bot):
	cog = Bookmarks(bot)
	await bot.add_cog(cog)
	bot.tree.remove_command("Bookmark", type=discord.AppCommandType.message)
	bot.tree.remove_command("bookmark")
	bot.tree.add_command(
		app_commands.ContextMenu(name="Bookmark", callback=cog.bookmark_message)
	)
	bot.tree.add_command(bookmark_group)