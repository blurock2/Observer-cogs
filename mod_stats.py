from __future__ import annotations

import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "bot.db"


class ModerationStatsStore:
    VALID_ACTIONS = {
        "reports_claimed",
        "kicks",
        "bans",
        "timeouts",
    }

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = str(db_path)
        self._init_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_table(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS moderation_stats (
                    guild_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    reports_claimed INTEGER NOT NULL DEFAULT 0,
                    kicks INTEGER NOT NULL DEFAULT 0,
                    bans INTEGER NOT NULL DEFAULT 0,
                    timeouts INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, moderator_id)
                )
                """
            )

    def increment(
        self,
        guild_id: int,
        moderator_id: int,
        action: str,
    ) -> None:
        if action not in self.VALID_ACTIONS:
            raise ValueError(f"Unknown moderation stat action: {action}")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO moderation_stats (
                    guild_id,
                    moderator_id,
                    reports_claimed,
                    kicks,
                    bans,
                    timeouts
                )
                VALUES (?, ?, 0, 0, 0, 0)
                ON CONFLICT(guild_id, moderator_id) DO NOTHING
                """,
                (guild_id, moderator_id),
            )

            conn.execute(
                f"""
                UPDATE moderation_stats
                SET {action} = {action} + 1
                WHERE guild_id = ? AND moderator_id = ?
                """,
                (guild_id, moderator_id),
            )

    def get(self, guild_id: int, moderator_id: int) -> dict[str, int]:
        defaults = {
            "reports_claimed": 0,
            "kicks": 0,
            "bans": 0,
            "timeouts": 0,
        }

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT reports_claimed, kicks, bans, timeouts
                FROM moderation_stats
                WHERE guild_id = ? AND moderator_id = ?
                """,
                (guild_id, moderator_id),
            ).fetchone()

        if row is None:
            return defaults

        return {
            "reports_claimed": int(row["reports_claimed"]),
            "kicks": int(row["kicks"]),
            "bans": int(row["bans"]),
            "timeouts": int(row["timeouts"]),
        }