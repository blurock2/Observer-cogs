from __future__ import annotations

import asyncio
import logging
import random
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks
from cogs.config import BOT_OWNER_ID, is_bot_owner


# Shared setup store (written by the /setup dashboard). setup_ui is
# loaded before this cog in bot.EXTENSIONS, so the import is safe.
from cogs.setup_ui import SetupConfigStore, DB_PATH, owner_or_has_permissions, owner_or_has_guild_permissions


# ============================================================ Configuration

BOT_OWNER_ID = 805687087784394773

BRAND_COLOR = discord.Color.from_rgb(150, 237, 241)
SUCCESS_COLOR = discord.Color.from_rgb(87, 242, 135)
ERROR_COLOR = discord.Color.from_rgb(237, 66, 69)
WARNING_COLOR = discord.Color.from_rgb(254, 231, 92)
GOLD_COLOR = discord.Color.from_rgb(255, 215, 0)
PURPLE_COLOR = discord.Color.from_rgb(155, 89, 182)

XP_COOLDOWN_SECONDS = 15
XP_REWARDS = [15, 20, 25, 30, 35, 40, 45, 50]

# Module key used by the /setup dashboard for this cog.
MODULE_KEY = "leveling"

# Keys mirrored between this cog's guild_config table and the shared
# setup store, so the dashboard and the slash commands stay in sync.
MIRRORED_KEYS = {
    "level_up_channel",
    "level_up_message",
    "weekly_channel",
    "weekly_day",
    "weekly_hour",
    "weekly_minute",
}

logger = logging.getLogger(__name__)


# ============================================================ Stable database path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "leveling.db"


def _db_path() -> Path:
    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    return DATABASE_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(_db_path()),
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


# ============================================================ Special level configuration

SPECIAL_LEVELS = {
    69: {
        "title": "😏 Nice! Level 69",
        "description": (
            "{user} reached **Level 69**!\n\n"
            "That is... a nice level."
        ),
        "color": discord.Color.from_rgb(
            255,
            105,
            180,
        ),
        "footer": "Nice.",
        "field_name": "😏 Special Milestone",
        "field_value": (
            "You unlocked the legendary Level 69 milestone."
        ),
    },
    420: {
        "title": "🚬 Level 420 Unlocked",
        "description": (
            "{user} reached **Level 420**!\n\n"
            "Blaze through the leaderboard."
        ),
        "color": discord.Color.from_rgb(
            88,
            101,
            242,
        ),
        "footer": "Level 420 achieved.",
        "field_name": "🚬 Special Milestone",
        "field_value": (
            "You reached the legendary Level 420 milestone."
        ),
    },
}


# ============================================================ Database backup

def _backup_database() -> Optional[Path]:
    database = _db_path()

    if not database.exists():
        return None

    backup_path = database.with_name(
        f"{database.stem}_backup_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"{database.suffix}"
    )

    shutil.copy2(database, backup_path)

    return backup_path


# ============================================================ Database migrations

def _add_missing_column(
    cur: sqlite3.Cursor,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    cur.execute(
        f"PRAGMA table_info({table_name})"
    )

    existing_columns = {
        row["name"]
        for row in cur.fetchall()
    }

    if column_name in existing_columns:
        return

    cur.execute(
        f"""
        ALTER TABLE {table_name}
        ADD COLUMN {column_name} {column_definition}
        """
    )

    logger.info(
        "Added missing database column %s.%s",
        table_name,
        column_name,
    )


# ============================================================ Database initialization

def _init_db() -> None:
    conn = _get_conn()

    try:
        cur = conn.cursor()

        # ============================================================ Users table

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 0,
                messages INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )

        # ============================================================ Weekly XP table

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_xp (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                xp INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, week_start)
            )
            """
        )

        # ============================================================ Guild configuration table

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                level_up_channel INTEGER,
                level_up_message TEXT NOT NULL
                    DEFAULT '🎉 {user} just reached level {level}!',
                weekly_channel INTEGER,
                weekly_day INTEGER NOT NULL DEFAULT 0,
                weekly_hour INTEGER NOT NULL DEFAULT 0,
                weekly_minute INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        _add_missing_column(
            cur,
            "guild_config",
            "level_up_channel",
            "INTEGER",
        )

        _add_missing_column(
            cur,
            "guild_config",
            "level_up_message",
            (
                "TEXT NOT NULL DEFAULT "
                "'🎉 {user} just reached level {level}!'"
            ),
        )

        _add_missing_column(
            cur,
            "guild_config",
            "weekly_channel",
            "INTEGER",
        )

        _add_missing_column(
            cur,
            "guild_config",
            "weekly_day",
            "INTEGER NOT NULL DEFAULT 0",
        )

        _add_missing_column(
            cur,
            "guild_config",
            "weekly_hour",
            "INTEGER NOT NULL DEFAULT 0",
        )

        _add_missing_column(
            cur,
            "guild_config",
            "weekly_minute",
            "INTEGER NOT NULL DEFAULT 0",
        )

        # ============================================================ Role rewards table

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS role_rewards (
                guild_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, level)
            )
            """
        )

        # ============================================================ Weekly post tracking

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_posts (
                guild_id INTEGER PRIMARY KEY,
                week_start TEXT NOT NULL
            )
            """
        )

        conn.commit()

        logger.info(
            "Leveling database initialized at %s",
            _db_path(),
        )

    finally:
        conn.close()


# ============================================================ Level calculations

def _xp_for_level(level: int) -> int:
    return 100 * (level ** 2)


def _next_level_xp(level: int) -> int:
    return _xp_for_level(level + 1)


def _level_from_xp(xp: int) -> int:
    if xp <= 0:
        return 0

    level = int((xp / 100) ** 0.5)

    while _xp_for_level(level + 1) <= xp:
        level += 1

    while _xp_for_level(level) > xp:
        level -= 1

    return level


def _current_week_start(now: datetime) -> str:
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


# ============================================================ Embed helpers

def themed_embed(
    *,
    title: str,
    description: Optional[str] = None,
    color: discord.Color = BRAND_COLOR,
    guild: Optional[discord.Guild] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    if guild is not None:
        if guild.icon:
            embed.set_author(
                name=guild.name,
                icon_url=guild.icon.url,
            )
        else:
            embed.set_author(name=guild.name)

    embed.set_footer(text="Leveling System")

    return embed


def error_embed(
    message: str,
    guild: Optional[discord.Guild] = None,
) -> discord.Embed:
    return themed_embed(
        title="❌ Something went wrong",
        description=message,
        color=ERROR_COLOR,
        guild=guild,
    )


def success_embed(
    message: str,
    guild: Optional[discord.Guild] = None,
) -> discord.Embed:
    return themed_embed(
        title="✅ Success",
        description=message,
        color=SUCCESS_COLOR,
        guild=guild,
    )


def progress_bar(
    current: int,
    maximum: int,
    length: int = 12,
) -> str:
    if maximum <= 0:
        maximum = 1

    percentage = max(
        0.0,
        min(current / maximum, 1.0),
    )

    filled = round(percentage * length)

    return (
        "▰" * filled
        + "▱" * (length - filled)
        + f" `{percentage:.0%}`"
    )


# ============================================================ Leveling cog

class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Shared config store (read by this cog, written by /setup).
        self.store = SetupConfigStore(DB_PATH)

        self._xp_cooldowns: Dict[
            Tuple[int, int],
            datetime,
        ] = {}

        self._xp_cooldown = timedelta(
            seconds=XP_COOLDOWN_SECONDS
        )

        self._db_lock = asyncio.Lock()

        backup_path = _backup_database()

        if backup_path is not None:
            logger.info(
                "Created leveling database backup at %s",
                backup_path,
            )

        _init_db()

        self._weekly_loop.start()


    # ============================================================ Cog lifecycle

    def cog_unload(self) -> None:
        self._weekly_loop.cancel()


    # ============================================================ User database operations

    async def _get_user_row(
        self,
        guild_id: int,
        user_id: int,
    ) -> sqlite3.Row:
        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    INSERT INTO users
                        (guild_id, user_id, xp, level, messages)
                    VALUES (?, ?, 0, 0, 0)
                    ON CONFLICT(guild_id, user_id)
                    DO NOTHING
                    """,
                    (
                        guild_id,
                        user_id,
                    ),
                )

                cur.execute(
                    """
                    SELECT xp, level, messages
                    FROM users
                    WHERE guild_id = ?
                      AND user_id = ?
                    """,
                    (
                        guild_id,
                        user_id,
                    ),
                )

                row = cur.fetchone()

                conn.commit()

                return row

            finally:
                conn.close()

    async def _add_xp(
        self,
        guild_id: int,
        user_id: int,
        xp: int,
    ) -> Tuple[int, int, int, int]:
        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    INSERT INTO users
                        (guild_id, user_id, xp, level, messages)
                    VALUES (?, ?, 0, 0, 0)
                    ON CONFLICT(guild_id, user_id)
                    DO NOTHING
                    """,
                    (
                        guild_id,
                        user_id,
                    ),
                )

                cur.execute(
                    """
                    SELECT xp, level, messages
                    FROM users
                    WHERE guild_id = ?
                      AND user_id = ?
                    """,
                    (
                        guild_id,
                        user_id,
                    ),
                )

                row = cur.fetchone()

                old_xp = row["xp"]
                old_level = row["level"]
                old_messages = row["messages"]

                new_xp = old_xp + xp
                new_level = _level_from_xp(new_xp)
                new_messages = old_messages + 1

                cur.execute(
                    """
                    UPDATE users
                    SET xp = ?,
                        level = ?,
                        messages = ?
                    WHERE guild_id = ?
                      AND user_id = ?
                    """,
                    (
                        new_xp,
                        new_level,
                        new_messages,
                        guild_id,
                        user_id,
                    ),
                )

                week_start = _current_week_start(
                    datetime.now(timezone.utc)
                )

                cur.execute(
                    """
                    INSERT INTO weekly_xp
                        (guild_id, user_id, week_start, xp)
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT(guild_id, user_id, week_start)
                    DO NOTHING
                    """,
                    (
                        guild_id,
                        user_id,
                        week_start,
                    ),
                )

                cur.execute(
                    """
                    UPDATE weekly_xp
                    SET xp = xp + ?
                    WHERE guild_id = ?
                      AND user_id = ?
                      AND week_start = ?
                    """,
                    (
                        xp,
                        guild_id,
                        user_id,
                        week_start,
                    ),
                )

                conn.commit()

                return (
                    old_level,
                    new_level,
                    old_xp,
                    new_xp,
                )

            finally:
                conn.close()


    # ============================================================ Guild configuration operations

    async def _get_guild_config(
        self,
        guild_id: int,
    ) -> Dict[str, Any]:
        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    INSERT INTO guild_config
                        (
                            guild_id,
                            level_up_channel,
                            level_up_message,
                            weekly_channel,
                            weekly_day,
                            weekly_hour,
                            weekly_minute
                        )
                    VALUES (
                        ?,
                        NULL,
                        '🎉 {user} just reached level {level}!',
                        NULL,
                        0,
                        0,
                        0
                    )
                    ON CONFLICT(guild_id)
                    DO NOTHING
                    """,
                    (guild_id,),
                )

                cur.execute(
                    """
                    SELECT
                        level_up_channel,
                        level_up_message,
                        weekly_channel,
                        weekly_day,
                        weekly_hour,
                        weekly_minute
                    FROM guild_config
                    WHERE guild_id = ?
                    """,
                    (guild_id,),
                )

                row = cur.fetchone()

                cur.execute(
                    """
                    SELECT level, role_id
                    FROM role_rewards
                    WHERE guild_id = ?
                    ORDER BY level ASC
                    """,
                    (guild_id,),
                )

                rewards = cur.fetchall()

                conn.commit()

                legacy = {
                    "level_up_channel": row["level_up_channel"],
                    "level_up_message": row["level_up_message"],
                    "weekly_channel": row["weekly_channel"],
                    "weekly_day": row["weekly_day"],
                    "weekly_hour": row["weekly_hour"],
                    "weekly_minute": row["weekly_minute"],
                }

            finally:
                conn.close()

        # Overlay dashboard store values: a key set via /setup wins,
        # otherwise fall back to the guild_config (legacy) value.
        stored = self.store.get_module(guild_id, MODULE_KEY)

        def overlay(key: str, legacy_key: str):
            value = stored.get(key)
            return value if value is not None else legacy[legacy_key]

        return {
            "level_up_channel": overlay("level_up_channel", "level_up_channel"),
            "level_up_message": overlay("level_up_message", "level_up_message"),
            "weekly_channel": overlay("weekly_channel", "weekly_channel"),
            "weekly_day": overlay("weekly_day", "weekly_day"),
            "weekly_hour": overlay("weekly_hour", "weekly_hour"),
            "weekly_minute": overlay("weekly_minute", "weekly_minute"),
            "role_rewards": {
                str(reward["level"]): reward["role_id"]
                for reward in rewards
            },
        }

    async def _set_guild_config(
        self,
        guild_id: int,
        **kwargs: Any,
    ) -> None:
        allowed_columns = {
            "level_up_channel",
            "level_up_message",
            "weekly_channel",
            "weekly_day",
            "weekly_hour",
            "weekly_minute",
        }

        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    INSERT INTO guild_config
                        (
                            guild_id,
                            level_up_channel,
                            level_up_message,
                            weekly_channel,
                            weekly_day,
                            weekly_hour,
                            weekly_minute
                        )
                    VALUES (
                        ?,
                        NULL,
                        '🎉 {user} just reached level {level}!',
                        NULL,
                        0,
                        0,
                        0
                    )
                    ON CONFLICT(guild_id)
                    DO NOTHING
                    """,
                    (guild_id,),
                )

                updates: List[str] = []
                params: List[Any] = []

                for key, value in kwargs.items():
                    if key not in allowed_columns:
                        continue

                    updates.append(f"{key} = ?")
                    params.append(value)

                if updates:
                    params.append(guild_id)

                    cur.execute(
                        f"""
                        UPDATE guild_config
                        SET {", ".join(updates)}
                        WHERE guild_id = ?
                        """,
                        params,
                    )

                if "role_rewards" in kwargs:
                    cur.execute(
                        """
                        DELETE FROM role_rewards
                        WHERE guild_id = ?
                        """,
                        (guild_id,),
                    )

                    for level, role_id in kwargs[
                        "role_rewards"
                    ].items():
                        cur.execute(
                            """
                            INSERT INTO role_rewards
                                (guild_id, level, role_id)
                            VALUES (?, ?, ?)
                            """,
                            (
                                guild_id,
                                int(level),
                                int(role_id),
                            ),
                        )

                conn.commit()

            finally:
                conn.close()

        # Mirror config keys into the shared store so /setup sees the
        # same values the slash commands write.
        for key, value in kwargs.items():
            if key in MIRRORED_KEYS:
                self.store.set(guild_id, MODULE_KEY, key, value)


    # ============================================================ Reset operations

    async def _reset_user_level(
        self,
        guild_id: int,
        user_id: int,
    ) -> None:
        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    DELETE FROM users
                    WHERE guild_id = ?
                      AND user_id = ?
                    """,
                    (
                        guild_id,
                        user_id,
                    ),
                )

                conn.commit()

            finally:
                conn.close()

    async def _reset_weekly_xp(
        self,
        guild_id: int,
        user_id: Optional[int] = None,
    ) -> None:
        week_start = _current_week_start(
            datetime.now(timezone.utc)
        )

        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                if user_id is None:
                    cur.execute(
                        """
                        DELETE FROM weekly_xp
                        WHERE guild_id = ?
                          AND week_start = ?
                        """,
                        (
                            guild_id,
                            week_start,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        DELETE FROM weekly_xp
                        WHERE guild_id = ?
                          AND user_id = ?
                          AND week_start = ?
                        """,
                        (
                            guild_id,
                            user_id,
                            week_start,
                        ),
                    )

                conn.commit()

            finally:
                conn.close()


    # ============================================================ Embed builders

    def _build_rank_embed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        xp: int,
        level: int,
        messages: int,
    ) -> discord.Embed:
        current_level_xp = _xp_for_level(level)
        next_level_xp = _next_level_xp(level)

        progress = max(
            0,
            xp - current_level_xp,
        )

        needed = max(
            1,
            next_level_xp - current_level_xp,
        )

        color = (
            member.color
            if member.color != discord.Color.default()
            else BRAND_COLOR
        )

        embed = themed_embed(
            title=f"📊 {member.display_name}'s Rank",
            description=(
                f"Current leveling progress for {member.mention}."
            ),
            color=color,
            guild=guild,
        )

        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(
            name="🏅 Level",
            value=f"```yaml\n{level}\n```",
            inline=True,
        )

        embed.add_field(
            name="✨ Total XP",
            value=f"```yaml\n{xp:,}\n```",
            inline=True,
        )

        embed.add_field(
            name="💬 Messages",
            value=f"```yaml\n{messages:,}\n```",
            inline=True,
        )

        embed.add_field(
            name="📈 Progress",
            value=(
                f"{progress_bar(progress, needed)}\n"
                f"`{progress:,} / {needed:,} XP`"
            ),
            inline=False,
        )

        embed.add_field(
            name="🎯 Next Level",
            value=(
                f"Level **{level + 1}** "
                f"at **{next_level_xp:,} XP**"
            ),
            inline=False,
        )

        return embed

    def _build_leaderboard_embed(
        self,
        guild: discord.Guild,
        rows: List[sqlite3.Row],
    ) -> discord.Embed:
        medals = ["🥇", "🥈", "🥉"]
        lines: List[str] = []

        for index, row in enumerate(rows, start=1):
            member = guild.get_member(row["user_id"])

            name = (
                member.display_name
                if member
                else f"<@{row['user_id']}>"
            )

            rank_icon = (
                medals[index - 1]
                if index <= len(medals)
                else f"`#{index}`"
            )

            lines.append(
                f"{rank_icon} **{name}**\n"
                f"┗ Level `{row['level']}` • "
                f"`{row['xp']:,} XP` • "
                f"`{row['messages']:,} messages`"
            )

        embed = themed_embed(
            title=f"🏆 {guild.name} Leaderboard",
            description="\n\n".join(lines),
            color=GOLD_COLOR,
            guild=guild,
        )

        embed.add_field(
            name="📌 Ranking",
            value="Sorted by level and total XP.",
            inline=False,
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        return embed

    def _build_weekly_rank_embed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        week_start: str,
        xp: int,
        rank: int,
    ) -> discord.Embed:
        embed = themed_embed(
            title=f"📅 {member.display_name}'s Weekly Rank",
            description=(
                f"Weekly activity for the week beginning "
                f"**{week_start} UTC**."
            ),
            color=PURPLE_COLOR,
            guild=guild,
        )

        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(
            name="⚡ Weekly XP",
            value=f"```yaml\n{xp:,} XP\n```",
            inline=True,
        )

        embed.add_field(
            name="🏆 Position",
            value=f"```yaml\n#{rank}\n```",
            inline=True,
        )

        embed.add_field(
            name="💡 Tip",
            value=(
                "Keep participating to climb the weekly leaderboard!"
            ),
            inline=False,
        )

        return embed


    # ============================================================ Level-up handling

    async def _give_level_up_rewards(
        self,
        guild: discord.Guild,
        user: discord.Member,
        old_level: int,
        new_level: int,
    ) -> None:
        config = await self._get_guild_config(guild.id)
        rewards = config.get("role_rewards", {})

        for level in range(old_level + 1, new_level + 1):
            role_id = rewards.get(str(level))

            if role_id is None:
                continue

            role = guild.get_role(int(role_id))

            if role is None:
                continue

            if guild.me is not None and role >= guild.me.top_role:
                continue

            try:
                await user.add_roles(
                    role,
                    reason=f"Reached level {level}",
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                logger.exception(
                    "Could not assign level %s reward role.",
                    level,
                )

    async def _send_level_up_embed(
        self,
        guild: discord.Guild,
        user: discord.Member,
        level: int,
    ) -> bool:
        config = await self._get_guild_config(guild.id)
        channel_id = config.get("level_up_channel")

        if channel_id is None:
            return False

        channel = guild.get_channel(int(channel_id))

        if not isinstance(channel, discord.TextChannel):
            return False

        special = SPECIAL_LEVELS.get(level)

        if special is not None:
            title = special["title"]

            description = special["description"].format(
                user=user.mention,
                level=level,
            )

            color = special["color"]
            footer_text = special["footer"]
            field_name = special["field_name"]
            field_value = special["field_value"]

        else:
            title = "🎉 Level Up!"

            message_template = (
                config.get("level_up_message")
                or "🎉 {user} just reached level {level}!"
            )

            try:
                description = message_template.format(
                    user=user.mention,
                    level=level,
                )
            except (KeyError, IndexError, ValueError):
                description = message_template

            color = SUCCESS_COLOR
            footer_text = (
                f"{guild.name} • Keep chatting to level up"
            )
            field_name = "🎁 Reward"
            field_value = (
                "Your level reward has been applied if one "
                "is configured."
            )

        embed = themed_embed(
            title=title,
            description=description,
            color=color,
            guild=guild,
        )

        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(
            name="✨ New Level",
            value=f"```yaml\nLevel {level}\n```",
            inline=True,
        )

        embed.add_field(
            name=field_name,
            value=field_value,
            inline=True,
        )

        embed.set_footer(text=footer_text)

        try:
            await channel.send(
                content=user.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )

            return True

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            logger.exception(
                "Could not send level-up embed."
            )
            return False


    # ============================================================ Weekly leaderboard operations

    async def _get_weekly_rows(
        self,
        guild_id: int,
        week_start: str,
    ) -> List[sqlite3.Row]:
        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    SELECT user_id, xp
                    FROM weekly_xp
                    WHERE guild_id = ?
                      AND week_start = ?
                    ORDER BY xp DESC
                    LIMIT 10
                    """,
                    (
                        guild_id,
                        week_start,
                    ),
                )

                return cur.fetchall()

            finally:
                conn.close()

    async def _was_weekly_post_sent(
        self,
        guild_id: int,
        week_start: str,
    ) -> bool:
        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    SELECT week_start
                    FROM weekly_posts
                    WHERE guild_id = ?
                    """,
                    (guild_id,),
                )

                row = cur.fetchone()

                return (
                    row is not None
                    and row["week_start"] == week_start
                )

            finally:
                conn.close()

    async def _mark_weekly_post_sent(
        self,
        guild_id: int,
        week_start: str,
    ) -> None:
        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    INSERT INTO weekly_posts
                        (guild_id, week_start)
                    VALUES (?, ?)
                    ON CONFLICT(guild_id)
                    DO UPDATE SET week_start = excluded.week_start
                    """,
                    (
                        guild_id,
                        week_start,
                    ),
                )

                conn.commit()

            finally:
                conn.close()

    async def _post_weekly_leaderboard(
        self,
        guild: discord.Guild,
        week_start: Optional[str] = None,
    ) -> bool:
        config = await self._get_guild_config(guild.id)
        channel_id = config.get("weekly_channel")

        if channel_id is None:
            return False

        channel = guild.get_channel(int(channel_id))

        if not isinstance(channel, discord.TextChannel):
            return False

        if week_start is None:
            today = datetime.now(timezone.utc).date()

            previous_monday = today - timedelta(
                days=today.weekday() + 7
            )

            week_start = previous_monday.isoformat()

        rows = await self._get_weekly_rows(
            guild.id,
            week_start,
        )

        if not rows:
            return False

        medals = ["🥇", "🥈", "🥉"]
        lines: List[str] = []

        for index, row in enumerate(rows, start=1):
            member = guild.get_member(row["user_id"])

            name = (
                member.display_name
                if member
                else f"<@{row['user_id']}>"
            )

            rank_icon = (
                medals[index - 1]
                if index <= len(medals)
                else f"`#{index}`"
            )

            lines.append(
                f"{rank_icon} **{name}** — "
                f"`{row['xp']:,} XP`"
            )

        embed = themed_embed(
            title="🏆 Weekly Leaderboard",
            description="\n".join(lines),
            color=PURPLE_COLOR,
            guild=guild,
        )

        embed.add_field(
            name="📅 Completed Week",
            value=f"`{week_start} UTC`",
            inline=False,
        )

        try:
            await channel.send(embed=embed)
            return True

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            logger.exception(
                "Could not send weekly leaderboard."
            )
            return False


    # ============================================================ Weekly scheduler

    @tasks.loop(minutes=1)
    async def _weekly_loop(self) -> None:
        await self.bot.wait_until_ready()

        try:
            now = datetime.now(timezone.utc)

            # Iterate every guild and post only when the configured weekday
            # and UTC time exactly matches the current minute.
            for guild in self.bot.guilds:
                try:
                    config = await self._get_guild_config(guild.id)

                    channel_id = config.get("weekly_channel")

                    if channel_id is None:
                        continue

                    day = config.get("weekly_day", 0) or 0
                    hour = config.get("weekly_hour", 0) or 0
                    minute = config.get("weekly_minute", 0) or 0

                    if now.weekday() != day:
                        continue

                    if now.hour != hour or now.minute != minute:
                        continue

                    today = now.date()
                    previous_monday = today - timedelta(
                        days=today.weekday() + 7
                    )

                    week_start = previous_monday.isoformat()

                    if await self._was_weekly_post_sent(
                        guild.id,
                        week_start,
                    ):
                        continue

                    posted = await self._post_weekly_leaderboard(
                        guild,
                        week_start,
                    )

                    if posted:
                        await self._mark_weekly_post_sent(
                            guild.id,
                            week_start,
                        )

                except Exception:
                    logger.exception(
                        "Weekly leaderboard error for guild %s",
                        guild.id,
                    )

        except Exception:
            logger.exception(
                "Weekly scheduler error."
            )

    @_weekly_loop.before_loop
    async def _before_weekly_loop(self) -> None:
        await self.bot.wait_until_ready()

    @_weekly_loop.error
    async def _weekly_loop_error(
        self,
        error: Exception,
    ) -> None:
        logger.exception(
            "Weekly leaderboard loop stopped.",
            exc_info=error,
        )


    # ============================================================ XP message listener

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:
        if message.author.bot:
            return

        if message.guild is None:
            return

        if not message.content and not message.attachments:
            return

        # Master switch: if leveling is disabled in /setup, earn no XP.
        # Defaults to on so existing behavior is preserved.
        guild_id = message.guild.id
        stored = self.store.get_module(guild_id, MODULE_KEY)

        if not bool(stored.get("enabled", True)):
            return

        now = datetime.now(timezone.utc)

        cooldown_key = (
            guild_id,
            message.author.id,
        )

        last_message = self._xp_cooldowns.get(cooldown_key)

        cooldown_seconds = stored.get("xp_cooldown", 15) or 15

        if (
            last_message is not None
            and now - last_message < timedelta(seconds=cooldown_seconds)
        ):
            return

        self._xp_cooldowns[cooldown_key] = now

        xp_gain = random.choice(XP_REWARDS)

        old_level, new_level, _, _ = await self._add_xp(
            message.guild.id,
            message.author.id,
            xp_gain,
        )

        if new_level <= old_level:
            return

        if not isinstance(message.author, discord.Member):
            return

        await self._give_level_up_rewards(
            message.guild,
            message.author,
            old_level,
            new_level,
        )

        for reached_level in range(
            old_level + 1,
            new_level + 1,
        ):
            await self._send_level_up_embed(
                message.guild,
                message.author,
                reached_level,
            )


    # ============================================================ Rank commands

    @app_commands.command(name="rank")
    @app_commands.describe(
        member="Member whose rank you want to view.",
    )
    async def rank_slash(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used inside a server."
                ),
                ephemeral=True,
            )
            return

        member = member or interaction.user

        if not isinstance(member, discord.Member):
            return

        row = await self._get_user_row(
            interaction.guild.id,
            member.id,
        )

        await interaction.response.send_message(
            embed=self._build_rank_embed(
                interaction.guild,
                member,
                row["xp"],
                row["level"],
                row["messages"],
            )
        )

    @commands.command(name="rank")
    async def rank_prefix(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        if ctx.guild is None:
            await ctx.send(
                embed=error_embed(
                    "This command can only be used inside a server."
                )
            )
            return

        member = member or ctx.author

        if not isinstance(member, discord.Member):
            return

        row = await self._get_user_row(
            ctx.guild.id,
            member.id,
        )

        await ctx.send(
            embed=self._build_rank_embed(
                ctx.guild,
                member,
                row["xp"],
                row["level"],
                row["messages"],
            )
        )


    # ============================================================ Leaderboard commands

    async def _get_leaderboard_rows(
        self,
        guild_id: int,
    ) -> List[sqlite3.Row]:
        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    SELECT user_id, xp, level, messages
                    FROM users
                    WHERE guild_id = ?
                    ORDER BY level DESC, xp DESC
                    LIMIT 10
                    """,
                    (guild_id,),
                )

                return cur.fetchall()

            finally:
                conn.close()

    @app_commands.command(name="leaderboard")
    async def leaderboard_slash(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used inside a server."
                ),
                ephemeral=True,
            )
            return

        rows = await self._get_leaderboard_rows(
            interaction.guild.id
        )

        if not rows:
            await interaction.response.send_message(
                embed=themed_embed(
                    title="📭 No Leveling Data",
                    description=(
                        "Nobody has earned XP in this server yet."
                    ),
                    color=WARNING_COLOR,
                    guild=interaction.guild,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=self._build_leaderboard_embed(
                interaction.guild,
                rows,
            )
        )

    @commands.command(name="leaderboard")
    async def leaderboard_prefix(
        self,
        ctx: commands.Context,
    ) -> None:
        if ctx.guild is None:
            await ctx.send(
                embed=error_embed(
                    "This command can only be used inside a server."
                )
            )
            return

        rows = await self._get_leaderboard_rows(
            ctx.guild.id
        )

        if not rows:
            await ctx.send(
                embed=themed_embed(
                    title="📭 No Leveling Data",
                    description=(
                        "Nobody has earned XP in this server yet."
                    ),
                    color=WARNING_COLOR,
                    guild=ctx.guild,
                )
            )
            return

        await ctx.send(
            embed=self._build_leaderboard_embed(
                ctx.guild,
                rows,
            )
        )


    # ============================================================ Weekly rank commands

    async def _get_weekly_rank(
        self,
        guild_id: int,
        user_id: int,
    ) -> Tuple[str, int, int]:
        week_start = _current_week_start(
            datetime.now(timezone.utc)
        )

        async with self._db_lock:
            conn = _get_conn()

            try:
                cur = conn.cursor()

                cur.execute(
                    """
                    SELECT xp
                    FROM weekly_xp
                    WHERE guild_id = ?
                      AND user_id = ?
                      AND week_start = ?
                    """,
                    (
                        guild_id,
                        user_id,
                        week_start,
                    ),
                )

                row = cur.fetchone()
                xp = row["xp"] if row else 0

                cur.execute(
                    """
                    SELECT COUNT(*) AS better_users
                    FROM weekly_xp
                    WHERE guild_id = ?
                      AND week_start = ?
                      AND xp > ?
                    """,
                    (
                        guild_id,
                        week_start,
                        xp,
                    ),
                )

                rank = int(
                    cur.fetchone()["better_users"]
                ) + 1

                return week_start, xp, rank

            finally:
                conn.close()

    @app_commands.command(name="weekly_rank")
    @app_commands.describe(
        member="Member whose weekly rank you want to view.",
    )
    async def weekly_rank_slash(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used inside a server."
                ),
                ephemeral=True,
            )
            return

        member = member or interaction.user

        if not isinstance(member, discord.Member):
            return

        week_start, xp, rank = await self._get_weekly_rank(
            interaction.guild.id,
            member.id,
        )

        await interaction.response.send_message(
            embed=self._build_weekly_rank_embed(
                interaction.guild,
                member,
                week_start,
                xp,
                rank,
            )
        )

    @commands.command(name="weekly_rank")
    async def weekly_rank_prefix(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        if ctx.guild is None:
            await ctx.send(
                embed=error_embed(
                    "This command can only be used inside a server."
                )
            )
            return

        member = member or ctx.author

        if not isinstance(member, discord.Member):
            return

        week_start, xp, rank = await self._get_weekly_rank(
            ctx.guild.id,
            member.id,
        )

        await ctx.send(
            embed=self._build_weekly_rank_embed(
                ctx.guild,
                member,
                week_start,
                xp,
                rank,
            )
        )


    # ============================================================ Configuration commands

    @staticmethod
    def _parse_time(
        value: str,
    ) -> Optional[Tuple[int, int]]:
        try:
            hour_text, minute_text = value.strip().split(":")
            hour = int(hour_text)
            minute = int(minute_text)

            if not 0 <= hour <= 23:
                return None

            if not 0 <= minute <= 59:
                return None

            return hour, minute

        except (
            ValueError,
            AttributeError,
        ):
            return None

    @app_commands.command(name="weekly_config")
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Channel for the weekly leaderboard.",
        day="Day: 0=Monday through 6=Sunday.",
        time="UTC time in HH:MM format.",
    )
    async def weekly_config_slash(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        day: int,
        time: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used inside a server."
                ),
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=error_embed(
                    "You need the **Manage Server** permission.",
                    interaction.guild,
                ),
                ephemeral=True,
            )
            return

        parsed_time = self._parse_time(time)

        if not 0 <= day <= 6 or parsed_time is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Use a day from `0` to `6` and time `HH:MM`.",
                    interaction.guild,
                ),
                ephemeral=True,
            )
            return

        hour, minute = parsed_time

        await self._set_guild_config(
            interaction.guild.id,
            weekly_channel=channel.id,
            weekly_day=day,
            weekly_hour=hour,
            weekly_minute=minute,
        )

        await interaction.response.send_message(
            embed=success_embed(
                f"Weekly leaderboards will be posted in "
                f"{channel.mention} at "
                f"{hour:02d}:{minute:02d} UTC.",
                interaction.guild,
            ),
            ephemeral=True,
        )

    @commands.command(name="weekly_config")
    @owner_or_has_guild_permissions(manage_guild=True)
    async def weekly_config_prefix(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        day: int,
        time: str,
    ) -> None:
        if ctx.guild is None:
            return

        parsed_time = self._parse_time(time)

        if not 0 <= day <= 6 or parsed_time is None:
            await ctx.send(
                embed=error_embed(
                    "Use a day from `0` to `6` and time `HH:MM`.",
                    ctx.guild,
                )
            )
            return

        hour, minute = parsed_time

        await self._set_guild_config(
            ctx.guild.id,
            weekly_channel=channel.id,
            weekly_day=day,
            weekly_hour=hour,
            weekly_minute=minute,
        )

        await ctx.send(
            embed=success_embed(
                f"Weekly leaderboards will be posted in "
                f"{channel.mention} at "
                f"{hour:02d}:{minute:02d} UTC.",
                ctx.guild,
            )
        )


    # ============================================================ Weekly reset commands

    @app_commands.command(name="weekly_reset")
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    @app_commands.describe(
        member="Member to reset, or empty for the whole server.",
    )
    async def weekly_reset_slash(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used inside a server."
                ),
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=error_embed(
                    "You need the **Manage Server** permission.",
                    interaction.guild,
                ),
                ephemeral=True,
            )
            return

        await self._reset_weekly_xp(
            interaction.guild.id,
            member.id if member else None,
        )

        target = (
            member.mention
            if member
            else "everyone in this server"
        )

        await interaction.response.send_message(
            embed=success_embed(
                f"Weekly XP was reset for {target}.",
                interaction.guild,
            ),
            ephemeral=True,
        )

    @commands.command(name="weekly_reset")
    @owner_or_has_guild_permissions(manage_guild=True)
    async def weekly_reset_prefix(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        if ctx.guild is None:
            return

        await self._reset_weekly_xp(
            ctx.guild.id,
            member.id if member else None,
        )

        target = (
            member.mention
            if member
            else "everyone in this server"
        )

        await ctx.send(
            embed=success_embed(
                f"Weekly XP was reset for {target}.",
                ctx.guild,
            )
        )


    # ============================================================ Role reward commands

    @app_commands.command(name="level_role_reward")
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    @app_commands.describe(
        level="Level at which to grant the role.",
        role="Role to grant at that level.",
    )
    async def level_role_reward_slash(
        self,
        interaction: discord.Interaction,
        level: int,
        role: discord.Role,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used inside a server."
                ),
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=error_embed(
                    "You need the **Manage Server** permission.",
                    interaction.guild,
                ),
                ephemeral=True,
            )
            return

        if level <= 0:
            await interaction.response.send_message(
                embed=error_embed(
                    "The level must be greater than `0`.",
                    interaction.guild,
                ),
                ephemeral=True,
            )
            return

        if role.is_default():
            await interaction.response.send_message(
                embed=error_embed(
                    "The @everyone role cannot be a reward.",
                    interaction.guild,
                ),
                ephemeral=True,
            )
            return

        config = await self._get_guild_config(
            interaction.guild.id
        )

        rewards = config.get("role_rewards", {})
        rewards[str(level)] = role.id

        await self._set_guild_config(
            interaction.guild.id,
            role_rewards=rewards,
        )

        await interaction.response.send_message(
            embed=success_embed(
                f"{role.mention} will be awarded at "
                f"**Level {level}**.",
                interaction.guild,
            ),
            ephemeral=True,
        )

    @commands.command(name="level_role_reward")
    @owner_or_has_guild_permissions(manage_guild=True)
    async def level_role_reward_prefix(
        self,
        ctx: commands.Context,
        level: int,
        role: discord.Role,
    ) -> None:
        if ctx.guild is None:
            return

        if level <= 0 or role.is_default():
            await ctx.send(
                embed=error_embed(
                    "Use a positive level and a normal server role.",
                    ctx.guild,
                )
            )
            return

        config = await self._get_guild_config(ctx.guild.id)

        rewards = config.get("role_rewards", {})
        rewards[str(level)] = role.id

        await self._set_guild_config(
            ctx.guild.id,
            role_rewards=rewards,
        )

        await ctx.send(
            embed=success_embed(
                f"{role.mention} will be awarded at "
                f"**Level {level}**.",
                ctx.guild,
            )
        )


    # ============================================================ Total level reset

    @commands.command(name="reset_level")
    @owner_or_has_guild_permissions(manage_guild=True)
    async def reset_level(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ) -> None:
        if ctx.guild is None:
            return

        await self._reset_user_level(
            ctx.guild.id,
            member.id,
        )

        await ctx.send(
            embed=success_embed(
                f"All leveling data for {member.mention} "
                "has been reset.",
                ctx.guild,
            )
        )


    # ============================================================ Owner test command

    @commands.command(name="test_levelup")
    async def test_levelup(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        level: int = 1,
    ) -> None:
        if ctx.author.id != BOT_OWNER_ID:
            await ctx.send(
                embed=error_embed(
                    "You are not authorized to use this command.",
                    ctx.guild,
                ),
                delete_after=5,
            )
            return

        if ctx.guild is None:
            await ctx.send(
                embed=error_embed(
                    "This command can only be used inside a server."
                ),
                delete_after=5,
            )
            return

        if level <= 0:
            await ctx.send(
                embed=error_embed(
                    "The test level must be greater than `0`.",
                    ctx.guild,
                ),
                delete_after=5,
            )
            return

        member = member or ctx.author

        if not isinstance(member, discord.Member):
            return

        sent = await self._send_level_up_embed(
            ctx.guild,
            member,
            level,
        )

        if not sent:
            await ctx.send(
                embed=error_embed(
                    "No valid level-up channel is configured. "
                    "Use `/setup_ui` to set one up first.",
                    ctx.guild,
                ),
                delete_after=8,
            )
            return

        await ctx.send(
            embed=success_embed(
                f"Sent a test level-up embed for {member.mention} ."
                f"at **Level {level}**.",
                ctx.guild,
            ),
            delete_after=5,
        )


# ============================================================ Extension setup

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leveling(bot))