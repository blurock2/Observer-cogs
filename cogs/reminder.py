from __future__ import annotations

from error_handling import ErrorHandledView, ObserverCog

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore
from database import connect_sqlite


TIME_PATTERN = re.compile(
	r"^(?:in\s+)?(?P<amount>[0-9]+(?:\.[0-9]+)?)\s*"
	r"(?P<unit>s|sec|secs|second|seconds|m|min|mins|minute|minutes|"
	r"h|hr|hrs|hour|hours|d|day|days)$",
	re.IGNORECASE,
)
TIME_UNITS = {
	"s": 1,
	"sec": 1,
	"secs": 1,
	"second": 1,
	"seconds": 1,
	"m": 60,
	"min": 60,
	"mins": 60,
	"minute": 60,
	"minutes": 60,
	"h": 60 * 60,
	"hr": 60 * 60,
	"hrs": 60 * 60,
	"hour": 60 * 60,
	"hours": 60 * 60,
	"d": 24 * 60 * 60,
	"day": 24 * 60 * 60,
	"days": 24 * 60 * 60,
}
MAX_DELAY_SECONDS = 31 * 24 * 60 * 60
REMINDER_REPEAT_SECONDS = 60 * 60
REMINDER_POLL_SECONDS = 10
REMINDER_RETRY_SECONDS = 5 * 60
logger = logging.getLogger("observer.reminder")


def parse_delay(value: str) -> float | None:
	match = TIME_PATTERN.fullmatch(value.strip())
	if match is None:
		return None

	amount = float(match.group("amount"))
	delay = amount * TIME_UNITS[match.group("unit").lower()]

	if delay <= 0 or delay > MAX_DELAY_SECONDS:
		return None

	return delay


class ReminderSeenView(ErrorHandledView):
	def __init__(self, cog: Reminder, reminder_id: int):
		super().__init__(timeout=None)
		self.cog = cog
		self.reminder_id = reminder_id

		button = discord.ui.Button(
			label="Seen",
			style=discord.ButtonStyle.success,
			custom_id=f"reminder:seen:{reminder_id}",
		)
		button.callback = self._confirm
		self.add_item(button)

	async def _confirm(self, interaction: discord.Interaction) -> None:
		reminder = self.cog._get_reminder(self.reminder_id)
		if reminder is None:
			await interaction.response.send_message(
				"This reminder is no longer active.",
				ephemeral=True,
			)
			return

		if reminder["user_id"] != interaction.user.id:
			await interaction.response.send_message(
				"Only the reminder owner can confirm it.",
				ephemeral=True,
			)
			return

		self.cog._acknowledge_reminder(self.reminder_id)
		for child in self.children:
			if isinstance(child, discord.ui.Button):
				child.disabled = True
				child.label = "Seen"

		await interaction.response.edit_message(view=self)


class Reminder(ObserverCog):
	"""Create short-lived reminders from a message or inline text."""

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._database_path = DB_PATH
		self._scheduler_task: asyncio.Task[None] | None = None
		self._init_database()

	async def cog_load(self) -> None:
		for reminder_id in self._pending_reminder_ids():
			self.bot.add_view(ReminderSeenView(self, reminder_id))

		self._scheduler_task = asyncio.create_task(
			self._scheduler_loop()
		)

	def cog_unload(self) -> None:
		if self._scheduler_task is not None:
			self._scheduler_task.cancel()
			self._scheduler_task = None

	def _init_database(self) -> None:
		with connect_sqlite(self._database_path) as connection:
			connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS reminders (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					user_id INTEGER NOT NULL,
					content TEXT NOT NULL,
					next_send_at REAL NOT NULL,
					acknowledged INTEGER NOT NULL DEFAULT 0,
					acknowledged_at REAL,
					created_at REAL NOT NULL
				)
				"""
			)
			columns = {
				row["name"]
				for row in connection.execute(
					"PRAGMA table_info(reminders)"
				)
			}
			for column, definition in (("guild_id", "INTEGER"), ("channel_id", "INTEGER"), ("source_url", "TEXT"), ("source_content", "TEXT"), ("source_author_id", "INTEGER"), ("source_channel_id", "INTEGER")):
				if column not in columns:
					connection.execute(f"ALTER TABLE reminders ADD COLUMN {column} {definition}")
			if "acknowledged_at" not in columns:
				connection.execute(
					"ALTER TABLE reminders ADD COLUMN acknowledged_at REAL"
				)
			connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS reminder_maintenance (
					name TEXT PRIMARY KEY,
					last_run_at REAL NOT NULL
				)
				"""
			)

	def _create_reminder(
		self,
		user_id: int,
		content: str,
		delay: float,
	) -> int:
		now = time.time()
		with connect_sqlite(self._database_path) as connection:
			cursor = connection.execute(
				"""
				INSERT INTO reminders
				(user_id, content, next_send_at, created_at)
				VALUES (?, ?, ?, ?)
				""",
				(user_id, content, now + delay, now),
			)
			return int(cursor.lastrowid)

	def _pending_reminder_ids(self) -> list[int]:
		with connect_sqlite(self._database_path) as connection:
			rows = connection.execute(
				"SELECT id FROM reminders WHERE acknowledged = 0"
			).fetchall()
		return [int(row["id"]) for row in rows]

	def _get_due_reminders(self):
		with connect_sqlite(self._database_path) as connection:
			return connection.execute(
				"""
				SELECT id, user_id, content, guild_id, channel_id
				FROM reminders
				WHERE acknowledged = 0 AND next_send_at <= ?
				""",
				(time.time(),),
			).fetchall()

	def _get_reminder(self, reminder_id: int):
		with connect_sqlite(self._database_path) as connection:
			return connection.execute(
				"""
				SELECT id, user_id, content, acknowledged
				FROM reminders
				WHERE id = ?
				""",
				(reminder_id,),
			).fetchone()

	def _set_next_send(self, reminder_id: int, when: float) -> None:
		with connect_sqlite(self._database_path) as connection:
			connection.execute(
				"""
				UPDATE reminders
				SET next_send_at = ?
				WHERE id = ? AND acknowledged = 0
				""",
				(when, reminder_id),
			)

	def _acknowledge_reminder(self, reminder_id: int) -> None:
		with connect_sqlite(self._database_path) as connection:
			connection.execute(
				"""
				UPDATE reminders
				SET acknowledged = 1, acknowledged_at = ?
				WHERE id = ?
				""",
				(time.time(), reminder_id),
			)

	@staticmethod
	def _sunday_start(now: datetime | None = None) -> float:
		current = now or datetime.now(timezone.utc)
		midnight = current.replace(
			hour=0,
			minute=0,
			second=0,
			microsecond=0,
		)
		days_since_sunday = (current.weekday() + 1) % 7
		return (
			midnight - timedelta(days=days_since_sunday)
		).timestamp()

	def _cleanup_acknowledged_reminders(self) -> int:
		week_start = self._sunday_start()
		with connect_sqlite(self._database_path) as connection:
			row = connection.execute(
				"""
				SELECT last_run_at
				FROM reminder_maintenance
				WHERE name = 'weekly_acknowledged_cleanup'
				"""
			).fetchone()
			if row is not None and row["last_run_at"] >= week_start:
				return 0

			deleted = connection.execute(
				"DELETE FROM reminders WHERE acknowledged = 1"
			).rowcount
			connection.execute(
				"""
				INSERT INTO reminder_maintenance (name, last_run_at)
				VALUES ('weekly_acknowledged_cleanup', ?)
				ON CONFLICT(name) DO UPDATE SET last_run_at = excluded.last_run_at
				""",
				(week_start,),
			)
			return deleted

	async def _scheduler_loop(self) -> None:
		while True:
			self._cleanup_acknowledged_reminders()
			for reminder in self._get_due_reminders():
				await self._send_reminder(reminder)
			await asyncio.sleep(REMINDER_POLL_SECONDS)

	async def _send_reminder(self, reminder) -> None:
		reminder_id = int(reminder["id"])

		try:
			user = self.bot.get_user(int(reminder["user_id"]))
			if user is None:
				user = await self.bot.fetch_user(int(reminder["user_id"]))

			dm_channel = await user.create_dm()
			await dm_channel.send(
				f"{user.mention} reminder: {reminder['content']}",
			allowed_mentions=discord.AllowedMentions(
				users=True,
				roles=False,
				everyone=False,
			),
			view=ReminderSeenView(self, reminder_id),
			)
		except (discord.Forbidden, discord.HTTPException) as error:
			logger.warning(
				"Could not DM reminder %s to user %s: %s",
				reminder_id,
				reminder["user_id"],
				error,
			)
			if await self._send_fallback(reminder):
				self._set_next_send(reminder_id, time.time() + REMINDER_REPEAT_SECONDS)
				return
			self._set_next_send(
				reminder_id,
				time.time() + REMINDER_RETRY_SECONDS,
			)
			return

		self._set_next_send(
			reminder_id,
			time.time() + REMINDER_REPEAT_SECONDS,
		)

	async def _send_fallback(self, reminder) -> bool:
		guild_id, channel_id = reminder["guild_id"], reminder["channel_id"]
		if not guild_id or not channel_id:
			return False
		store = SetupConfigStore(self._database_path)
		if not store.get(guild_id, "reminders", "fallback_enabled", False):
			return False
		guild = self.bot.get_guild(guild_id)
		channel = guild.get_channel_or_thread(channel_id) if guild else None
		if channel is None:
			return False
		try:
			member = guild.get_member(reminder["user_id"]) or await guild.fetch_member(reminder["user_id"])
			if not channel.permissions_for(member).view_channel:
				return False
			await channel.send(
				f"{member.mention} reminder: {reminder['content']}",
				allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
				view=ReminderSeenView(self, int(reminder["id"])),
			)
		except (discord.Forbidden, discord.HTTPException):
			return False
		return True

	def _schedule_reminder(
		self,
		user: discord.abc.User,
		reminder_text: str,
		delay: float,
	) -> None:
		reminder_id = self._create_reminder(
			user.id,
			reminder_text,
			delay,
		)
		self.bot.add_view(ReminderSeenView(self, reminder_id))

	@staticmethod
	def _is_duration_response(
		message: discord.Message,
		user_id: int,
		channel_id: int,
	) -> bool:
		return (
			not message.author.bot
			and message.author.id == user_id
			and message.channel.id == channel_id
		)

	async def _get_reminder_text(
		self,
		ctx: commands.Context,
		text: str,
	) -> str | None:
		if text.strip():
			return text.strip()

		reference = ctx.message.reference
		if reference is None or reference.message_id is None:
			return None

		referenced = reference.resolved
		if not isinstance(referenced, discord.Message):
			try:
				referenced = await ctx.channel.fetch_message(
					reference.message_id
				)
			except (discord.NotFound, discord.Forbidden, discord.HTTPException):
				return None

		content = referenced.content.strip()
		return content or None

	@commands.command(
		name="remind",
		help="Set a reminder from text or a replied-to message.",
	)
	async def remind_prefix(
		self,
		ctx: commands.Context,
		*,
		text: str = "",
	) -> None:
		reminder_text = await self._get_reminder_text(ctx, text)
		if reminder_text is None:
			await ctx.send(
				"Reply to a message or add text after `!remind`."
			)
			return

		await ctx.send(
			"When should I send it? Reply with a delay such as `10m`, "
			"`2h`, or `1 day`. Type `cancel` to stop."
		)

		def check(message: discord.Message) -> bool:
			return self._is_duration_response(
				message,
				ctx.author.id,
				ctx.channel.id,
			)

		try:
			response = await self.bot.wait_for(
				"message",
				timeout=60,
				check=check,
			)
		except asyncio.TimeoutError:
			await ctx.send("Reminder cancelled because no time was provided.")
			return

		if response.content.strip().lower() == "cancel":
			await ctx.send("Reminder cancelled.")
			return

		delay = parse_delay(response.content)
		if delay is None:
			await ctx.send(
				"I could not understand that delay. Use a value like `10m`, "
				"`2h`, or `1 day`, then run `!remind` again."
			)
			return

		self._schedule_reminder(
			ctx.author,
			reminder_text,
			delay,
		)

		await ctx.send(f"Reminder set for {response.content.strip()}.")

	@app_commands.command(
		name="reminder",
		description="Set a reminder and receive a mention when it is due.",
	)
	@app_commands.describe(
		text="The reminder text.",
		delay="Optional delay such as 10m, 2h, or 1 day.",
	)
	@app_commands.allowed_contexts(
		guilds=True,
		dms=True,
		private_channels=True,
	)
	async def reminder_slash(
		self,
		interaction: discord.Interaction,
		text: str,
		delay: str | None = None,
	) -> None:
		channel = interaction.channel
		if channel is None:
			await interaction.response.send_message(
				"I could not access this conversation.",
				ephemeral=True,
			)
			return

		if delay is None:
			await interaction.response.send_message(
				"When should I send it? Reply with `10m`, `2h`, or "
				"`1 day`. Type `cancel` to stop."
			)

			def check(message: discord.Message) -> bool:
				return self._is_duration_response(
					message,
					interaction.user.id,
					channel.id,
				)

			try:
				response = await self.bot.wait_for(
					"message",
					timeout=60,
					check=check,
				)
			except asyncio.TimeoutError:
				await channel.send(
					"Reminder cancelled because no time was provided."
				)
				return

			if response.content.strip().lower() == "cancel":
				await channel.send("Reminder cancelled.")
				return

			delay = response.content

		parsed_delay = parse_delay(delay)
		if parsed_delay is None:
			message = (
				"I could not understand that delay. Use `10m`, `2h`, "
				"or `1 day`; the maximum is 31 days."
			)
			if interaction.response.is_done():
				await channel.send(message)
			else:
				await interaction.response.send_message(message)
			return

		self._schedule_reminder(
			interaction.user,
			text.strip(),
			parsed_delay,
		)

		confirmation = f"Reminder set for {delay.strip()}."
		if interaction.response.is_done():
			await channel.send(confirmation)
		else:
			await interaction.response.send_message(confirmation)

	@app_commands.command(
		name="reminders",
		description="List your pending reminders privately.",
	)
	async def reminders(self, interaction: discord.Interaction) -> None:
		with connect_sqlite(self._database_path) as connection:
			rows = connection.execute(
				"SELECT id, content, next_send_at FROM reminders "
				"WHERE user_id = ? AND acknowledged = 0 ORDER BY next_send_at LIMIT 25",
				(interaction.user.id,),
			).fetchall()
		if not rows:
			await interaction.response.send_message("You have no pending reminders.", ephemeral=True)
			return
		text = "\n\n".join(
			f"#{row['id']} — {row['content']}\nIn <t:{int(row['next_send_at'])}:R>"
			for row in rows
		)
		await interaction.response.send_message(text, ephemeral=True)

	@app_commands.command(
		name="reminder-delete",
		description="Delete one of your pending reminders.",
	)
	async def reminder_delete(self, interaction: discord.Interaction, id: int) -> None:
		with connect_sqlite(self._database_path) as connection:
			result = connection.execute(
				"DELETE FROM reminders WHERE id = ? AND user_id = ?",
				(id, interaction.user.id),
			).rowcount
		await interaction.response.send_message(
			"Reminder deleted." if result else "Reminder not found.",
			ephemeral=True,
		)

	@app_commands.command(
		name="reminder-reschedule",
		description="Reschedule one of your pending reminders.",
	)
	async def reminder_reschedule(
		self,
		interaction: discord.Interaction,
		id: int,
		delay: str,
	) -> None:
		parsed_delay = parse_delay(delay)
		if parsed_delay is None:
			await interaction.response.send_message("Use a delay such as `20m` or `2h`.", ephemeral=True)
			return
		with connect_sqlite(self._database_path) as connection:
			result = connection.execute(
				"UPDATE reminders SET next_send_at = ?, acknowledged = 0, acknowledged_at = NULL "
				"WHERE id = ? AND user_id = ?",
				(time.time() + parsed_delay, id, interaction.user.id),
			).rowcount
		await interaction.response.send_message(
			"Reminder rescheduled." if result else "Reminder not found.",
			ephemeral=True,
		)

	async def reminder_context(
		self,
		interaction: discord.Interaction,
		message: discord.Message,
	) -> None:
		class ReminderModal(discord.ui.Modal, title="Remind me"):
			when = discord.ui.TextInput(label="When?", placeholder="2h")
			note = discord.ui.TextInput(label="Extra note", required=False, style=discord.TextStyle.paragraph)

			async def on_submit(modal_self, modal_interaction: discord.Interaction):
				delay = parse_delay(str(modal_self.when.value))
				if delay is None:
					await modal_interaction.response.send_message("Use a delay such as `20m` or `2h`.", ephemeral=True)
					return
				with connect_sqlite(self._database_path) as connection:
					cursor = connection.execute(
						"INSERT INTO reminders (user_id, content, next_send_at, created_at, guild_id, channel_id, source_url, source_content, source_author_id, source_channel_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
						(modal_interaction.user.id, str(modal_self.note.value).strip() or message.content or "Saved message", time.time() + delay, time.time(), message.guild.id if message.guild else None, message.channel.id, message.jump_url, message.content, message.author.id, message.channel.id),
					)
				self.bot.add_view(ReminderSeenView(self, int(cursor.lastrowid)))
				await modal_interaction.response.send_message("Reminder set.", ephemeral=True)

		await interaction.response.send_modal(ReminderModal())

async def setup(bot: commands.Bot) -> None:
	cog = Reminder(bot)
	await bot.add_cog(cog)
	bot.tree.add_command(app_commands.ContextMenu(name="Remind Me", callback=cog.reminder_context))
	# /reminder remains the existing top-level command; management uses /reminder-delete and /reminder-reschedule.
