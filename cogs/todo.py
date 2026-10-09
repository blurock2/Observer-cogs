from __future__ import annotations

import math
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH
from database import connect_sqlite


def _parse_due(value: str | None) -> float | None:
	if not value:
		return None
	try:
		parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
	except ValueError:
		return None
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=timezone.utc)
	return parsed.timestamp()


def _todo_line(row) -> str:
	mark = "x" if row["completed"] else " "
	text = f"[{mark}] #{row['id']} {row['text']}"
	if row["due_at"]:
		text += f"\n    Due: <t:{int(row['due_at'])}:f>"
	if row["completed_at"]:
		text += f"\n    Completed <t:{int(row['completed_at'])}:d>"
	return text


class TodoPager(discord.ui.View):
	def __init__(self, cog: Todo, user_id: int, page: int = 0):
		super().__init__(timeout=300)
		self.cog, self.user_id, self.page = cog, user_id, page
		rows = cog._list(user_id)
		self.pages = max(1, math.ceil(len(rows) / 5))
		previous = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary)
		previous.callback = self._previous
		next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
		next_button.callback = self._next
		previous.disabled = page <= 0
		next_button.disabled = page >= self.pages - 1
		self.add_item(previous)
		self.add_item(next_button)

	async def _render(self, interaction: discord.Interaction, page: int):
		rows = self.cog._list(self.user_id)
		start = page * 5
		embed = self.cog._embed(rows[start:start + 5], page, self.pages)
		await interaction.response.edit_message(embed=embed, view=TodoPager(self.cog, self.user_id, page))

	async def _previous(self, interaction: discord.Interaction):
		await self._render(interaction, self.page - 1)

	async def _next(self, interaction: discord.Interaction):
		await self._render(interaction, self.page + 1)


class Todo(commands.GroupCog, group_name="todo", group_description="Manage your private todo list"):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.db_path = DB_PATH
		with connect_sqlite(self.db_path) as db:
			db.execute("""CREATE TABLE IF NOT EXISTS todos (
				id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
				text TEXT NOT NULL, created_at REAL NOT NULL, due_at REAL,
				completed INTEGER NOT NULL DEFAULT 0, completed_at REAL)""")

	def _list(self, user_id: int):
		with connect_sqlite(self.db_path) as db:
			return db.execute("SELECT * FROM todos WHERE user_id = ? ORDER BY completed ASC, COALESCE(due_at, 9223372036), id", (user_id,)).fetchall()

	def _embed(self, rows, page: int = 0, pages: int = 1) -> discord.Embed:
		embed = discord.Embed(title="Your Todos", color=0x96EDF1)
		embed.description = "\n\n".join(_todo_line(row) for row in rows) or "No todos yet."
		if pages > 1:
			embed.set_footer(text=f"Page {page + 1}/{pages}")
		return embed

	@app_commands.command(name="add", description="Add a private todo.")
	@app_commands.describe(text="Todo text", due="ISO date/time, for example 2026-10-12T18:30Z")
	async def add(self, interaction: discord.Interaction, text: str, due: str | None = None):
		due_at = _parse_due(due)
		if due and due_at is None:
			await interaction.response.send_message("Use an ISO due date such as `2026-10-12T18:30Z`.", ephemeral=True)
			return
		with connect_sqlite(self.db_path) as db:
			cursor = db.execute("INSERT INTO todos (user_id, text, created_at, due_at) VALUES (?, ?, ?, ?)", (interaction.user.id, text.strip(), time.time(), due_at))
		await interaction.response.send_message(f"Todo #{cursor.lastrowid} added.", ephemeral=True)

	@app_commands.command(name="list", description="Show your private todos.")
	async def list(self, interaction: discord.Interaction):
		rows = self._list(interaction.user.id)
		await interaction.response.send_message(embed=self._embed(rows), view=TodoPager(self, interaction.user.id), ephemeral=True)

	async def _set_completed(self, interaction: discord.Interaction, todo_id: int, completed: bool):
		with connect_sqlite(self.db_path) as db:
			result = db.execute("UPDATE todos SET completed = ?, completed_at = ? WHERE id = ? AND user_id = ?", (int(completed), time.time() if completed else None, todo_id, interaction.user.id))
		await interaction.response.send_message("Todo updated." if result.rowcount else "That todo was not found.", ephemeral=True)

	@app_commands.command(name="complete", description="Complete one of your todos.")
	async def complete(self, interaction: discord.Interaction, id: int):
		await self._set_completed(interaction, id, True)

	@app_commands.command(name="uncomplete", description="Reopen one of your todos.")
	async def uncomplete(self, interaction: discord.Interaction, id: int):
		await self._set_completed(interaction, id, False)

	@app_commands.command(name="delete", description="Delete one of your todos.")
	async def delete(self, interaction: discord.Interaction, id: int):
		with connect_sqlite(self.db_path) as db:
			result = db.execute("DELETE FROM todos WHERE id = ? AND user_id = ?", (id, interaction.user.id))
		await interaction.response.send_message("Todo deleted." if result.rowcount else "That todo was not found.", ephemeral=True)

	@app_commands.command(name="edit", description="Edit one of your todos.")
	async def edit(self, interaction: discord.Interaction, id: int, new_text: str):
		with connect_sqlite(self.db_path) as db:
			result = db.execute("UPDATE todos SET text = ? WHERE id = ? AND user_id = ?", (new_text.strip(), id, interaction.user.id))
		await interaction.response.send_message("Todo updated." if result.rowcount else "That todo was not found.", ephemeral=True)

	@app_commands.command(name="clear", description="Delete all of your todos.")
	async def clear(self, interaction: discord.Interaction):
		with connect_sqlite(self.db_path) as db:
			db.execute("DELETE FROM todos WHERE user_id = ?", (interaction.user.id,))
		await interaction.response.send_message("Your todo list was cleared.", ephemeral=True)

	@app_commands.command(name="clear-completed", description="Delete your completed todos.")
	async def clear_completed(self, interaction: discord.Interaction):
		with connect_sqlite(self.db_path) as db:
			db.execute("DELETE FROM todos WHERE user_id = ? AND completed = 1", (interaction.user.id,))
		await interaction.response.send_message("Completed todos cleared.", ephemeral=True)


async def setup(bot: commands.Bot):
	await bot.add_cog(Todo(bot))