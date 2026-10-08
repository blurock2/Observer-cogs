"""Send sanitized, bounded batches of runtime errors to Observer Support."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import discord

logger = logging.getLogger("observer.error_reporting")


def new_error_id() -> str:
    return f"OBS-{uuid4().hex[:12].upper()}"


class ErrorIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "error_id"):
            record.error_id = new_error_id() if record.levelno >= logging.ERROR else "-"
        return True


def safe_name(value: str | None, limit: int = 60) -> str:
    # Only code identifiers, never exception text or log message contents.
    return re.sub(r"[^a-zA-Z0-9_. /-]", "", value or "")[:limit] or "unknown"


@dataclass(frozen=True)
class ErrorReport:
    error_id: str
    source: str
    exception: str
    location: str
    guild_id: int | None
    user_id: int | None
    occurred_at: int

    @classmethod
    def from_record(cls, record: logging.LogRecord) -> ErrorReport:
        ErrorIdFilter().filter(record)
        exception = "Runtime error"
        location = safe_name(record.funcName)
        if record.exc_info and record.exc_info[1] is not None:
            exception = safe_name(type(record.exc_info[1]).__name__, 40)
            tb = record.exc_info[2]
            if tb is not None:
                while tb.tb_next is not None:
                    tb = tb.tb_next
                location = f"{safe_name(tb.tb_frame.f_code.co_name, 40)}:{tb.tb_lineno}"
        return cls(record.error_id, safe_name(getattr(record, "error_source", record.name)),
                   exception, location, getattr(record, "error_guild_id", None),
                   getattr(record, "error_user_id", None), int(record.created))

    def line(self) -> str:
        context = f"server={self.guild_id or 'DM/unknown'} user={self.user_id or 'unknown'}"
        return (f"**`{self.error_id}`** • <t:{self.occurred_at}:T>\n"
                f"`{self.source}` • `{self.exception}` • `{self.location}`\n"
                f"{context}")


class DiscordErrorHandler(logging.Handler):
    def __init__(self, reporter: ErrorReporter):
        super().__init__(logging.ERROR)
        self.reporter = reporter
        self.addFilter(ErrorIdFilter())

    def emit(self, record: logging.LogRecord) -> None:
        # Reporting failures stay in the local logs; they cannot report themselves.
        if record.name == logger.name or record.name.startswith(logger.name + "."):
            return
        try:
            report = ErrorReport.from_record(record)
            self.reporter.loop.call_soon_threadsafe(self.reporter.enqueue, report)
        except Exception:
            self.handleError(record)


class ErrorReporter:
    BATCH_SIZE = 15
    INTERVAL = 5.0

    def __init__(self, bot, support_guild_id: int):
        self.bot = bot
        self.support_guild_id = support_guild_id
        self.queue: asyncio.Queue[ErrorReport] = asyncio.Queue(maxsize=200)
        self.handler: DiscordErrorHandler | None = None
        self.task: asyncio.Task | None = None
        self.loop = None
        self.dropped = 0

    def start(self) -> None:
        if not self.support_guild_id or self.task is not None:
            return
        self.loop = asyncio.get_running_loop()
        self.handler = DiscordErrorHandler(self)
        logging.getLogger().addHandler(self.handler)
        self.task = asyncio.create_task(self.run(), name="observer-error-reporter")

    def enqueue(self, report: ErrorReport) -> None:
        try:
            self.queue.put_nowait(report)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1:
                logger.warning("Error reporting queue full; further errors remain in VPS logs")

    async def send_batch(self, reports: list[ErrorReport]) -> None:
        try:
            channel_id = self.bot.setup_store.get(self.support_guild_id, "bot", "error_channel")
            if not channel_id:
                return
            guild = self.bot.get_guild(self.support_guild_id)
            if guild is None:
                return
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                channel = await guild.fetch_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.support_guild_id:
                return
            embed = discord.Embed(
                title="Observer error reports",
                description="\n\n".join(report.line() for report in reports),
                color=0xED4245,
                timestamp=datetime.now(timezone.utc),
            )
            footer = "Match an error ID with the VPS logs. Full traces and message contents stay private."
            if self.dropped:
                footer += f" {self.dropped} overflow reports stayed in VPS logs."
            embed.set_footer(text=footer)
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            self.dropped = 0
        except Exception:
            logger.exception("Could not send error reports to Observer Support; IDs remain in VPS logs")

    async def run(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            reports = [await self.queue.get()]
            # Batch repeats/bursts into at most one message every five seconds.
            await asyncio.sleep(self.INTERVAL)
            while len(reports) < self.BATCH_SIZE and not self.queue.empty():
                reports.append(self.queue.get_nowait())
            await self.send_batch(reports)

    async def close(self) -> None:
        if self.handler is not None:
            logging.getLogger().removeHandler(self.handler)
            self.handler.close()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
