import json
import logging
import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import time

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "bot.db"
logger = logging.getLogger("observer.database")

BACKUP_RETENTION_DAYS = 5
BACKUP_PATTERNS = (
    "*_backup_*.db",
    "*.pre-migration-backup",
    "*.backup-*",
    "migration_backup_*",
)


def connect_sqlite(
    database_path: str | Path,
    timeout: float = 10,
) -> sqlite3.Connection:
    """Open a project SQLite connection with contention-safe defaults."""
    connection = sqlite3.connect(database_path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def cleanup_old_backups(
    data_dir: str | Path = DATA_DIR,
    retention_days: int = BACKUP_RETENTION_DAYS,
) -> int:
    """Delete recognized backup files and directories older than the retention period."""
    backup_root = Path(data_dir)
    cutoff = time() - (retention_days * 24 * 60 * 60)
    deleted_count = 0

    if not backup_root.exists():
        return deleted_count

    for pattern in BACKUP_PATTERNS:
        for backup_path in backup_root.glob(pattern):
            try:
                if backup_path.stat().st_mtime >= cutoff:
                    continue

                if backup_path.is_dir():
                    shutil.rmtree(backup_path)
                else:
                    backup_path.unlink()

                deleted_count += 1
                logger.info("Deleted expired backup: %s", backup_path)
            except OSError:
                logger.warning(
                    "Could not delete expired backup: %s",
                    backup_path,
                    exc_info=True,
                )

    return deleted_count


def migrate_legacy_databases(primary_path: str | Path) -> None:
    """Copy feature databases into the primary database without data loss."""
    primary = Path(primary_path)
    primary.parent.mkdir(parents=True, exist_ok=True)

    legacy_paths = {
        "account_links": BASE_DIR / "data" / "account_links.sqlite3",
        "member_stats": BASE_DIR / "data" / "member_stats.sqlite3",
        "leveling": BASE_DIR / "data" / "leveling.db",
    }

    with closing(connect_sqlite(primary, timeout=30)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS database_migrations (
                name TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        for name, legacy_path in legacy_paths.items():
            if not legacy_path.exists() or legacy_path.resolve() == primary.resolve():
                continue

            migration_name = f"{name}_to_primary_v1"
            already_done = connection.execute(
                "SELECT 1 FROM database_migrations WHERE name = ?",
                (migration_name,),
            ).fetchone()
            if already_done:
                continue

            backup_path = legacy_path.with_name(
                f"{legacy_path.name}.pre-migration-backup"
            )
            if not backup_path.exists():
                shutil.copy2(legacy_path, backup_path)

            with closing(connect_sqlite(legacy_path)) as legacy_connection:
                legacy_connection.row_factory = sqlite3.Row
                if name == "account_links":
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS account_links (
                            user_id INTEGER NOT NULL,
                            platform TEXT NOT NULL,
                            username TEXT NOT NULL,
                            updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                            PRIMARY KEY (user_id, platform)
                        )
                        """
                    )
                    rows = legacy_connection.execute(
                        "SELECT user_id, platform, username, updated_at "
                        "FROM account_links"
                    ).fetchall()
                    connection.executemany(
                        "INSERT OR IGNORE INTO account_links "
                        "(user_id, platform, username, updated_at) VALUES (?, ?, ?, ?)",
                        [tuple(row) for row in rows],
                    )
                elif name == "member_stats":
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS message_stats (
                            guild_id INTEGER NOT NULL,
                            user_id INTEGER NOT NULL,
                            message_count INTEGER NOT NULL DEFAULT 0,
                            PRIMARY KEY (guild_id, user_id)
                        )
                        """
                    )
                    rows = legacy_connection.execute(
                        "SELECT guild_id, user_id, message_count "
                        "FROM message_stats"
                    ).fetchall()
                    connection.executemany(
                        "INSERT OR IGNORE INTO message_stats "
                        "(guild_id, user_id, message_count) VALUES (?, ?, ?)",
                        [tuple(row) for row in rows],
                    )
                else:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS users (
                            guild_id INTEGER NOT NULL,
                            user_id INTEGER NOT NULL,
                            xp INTEGER NOT NULL DEFAULT 0,
                            level INTEGER NOT NULL DEFAULT 0,
                            messages INTEGER NOT NULL DEFAULT 0,
                            PRIMARY KEY (guild_id, user_id)
                        );
                        CREATE TABLE IF NOT EXISTS weekly_xp (
                            guild_id INTEGER NOT NULL,
                            user_id INTEGER NOT NULL,
                            week_start TEXT NOT NULL,
                            xp INTEGER NOT NULL DEFAULT 0,
                            PRIMARY KEY (guild_id, user_id, week_start)
                        );
                        CREATE TABLE IF NOT EXISTS guild_config (
                            guild_id INTEGER PRIMARY KEY,
                            level_up_channel INTEGER,
                            level_up_message TEXT NOT NULL
                                DEFAULT '🎉 {user} just reached level {level}!',
                            weekly_channel INTEGER,
                            weekly_day INTEGER NOT NULL DEFAULT 0,
                            weekly_hour INTEGER NOT NULL DEFAULT 0,
                            weekly_minute INTEGER NOT NULL DEFAULT 0
                        );
                        CREATE TABLE IF NOT EXISTS role_rewards (
                            guild_id INTEGER NOT NULL,
                            level INTEGER NOT NULL,
                            role_id INTEGER NOT NULL,
                            PRIMARY KEY (guild_id, level)
                        );
                        CREATE TABLE IF NOT EXISTS weekly_posts (
                            guild_id INTEGER PRIMARY KEY,
                            week_start TEXT NOT NULL
                        );
                        """
                    )
                    for table in (
                        "users", "weekly_xp", "guild_config", "role_rewards", "weekly_posts"
                    ):
                        rows = legacy_connection.execute(
                            f"SELECT * FROM {table}"
                        ).fetchall()
                        if not rows:
                            continue
                        placeholders = ", ".join("?" for _ in rows[0])
                        connection.execute(
                            f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})",
                            tuple(rows[0]),
                        )
                        if len(rows) > 1:
                            connection.executemany(
                                f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})",
                                [tuple(row) for row in rows[1:]],
                            )

                connection.execute(
                    "INSERT INTO database_migrations (name) VALUES (?)",
                    (migration_name,),
                )

        connection.commit()

    logger.info("Legacy database migration completed into %s", primary)


def init_database():
    DATA_DIR.mkdir(exist_ok=True)

    connection = connect_sqlite(DATABASE_PATH)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            report_message_id INTEGER,
            reported_message_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            reported_user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            claimed_by INTEGER,
            claimed_at TEXT,
            resolved_by INTEGER,
            resolved_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    connection.close()

    logger.info("Reports database ready: %s", DATABASE_PATH)


class ModerationActionStore:
    """Persist moderation actions that can be undone after a restart."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init()

    @contextmanager
    def _connect(self):
        connection = connect_sqlite(self.db_path)
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS moderation_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    target_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    log_message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    undone_at TEXT,
                    undone_by INTEGER
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_moderation_actions_log_message
                ON moderation_actions(log_message_id)
                """
            )

    def create(
        self,
        guild_id: int,
        action_type: str,
        target_id: int,
        moderator_id: int,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO moderation_actions
                    (guild_id, action_type, target_id, moderator_id)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, action_type, target_id, moderator_id),
            )
            return int(cursor.lastrowid)

    def set_log_message(self, action_id: int, message_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE moderation_actions SET log_message_id = ? WHERE id = ?",
                (message_id, action_id),
            )

    def get_by_log_message(self, message_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM moderation_actions WHERE log_message_id = ?",
                (message_id,),
            ).fetchone()

    def claim(self, action_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE moderation_actions
                SET status = 'undoing'
                WHERE id = ? AND status = 'active'
                """,
                (action_id,),
            )
            return cursor.rowcount == 1

    def release(self, action_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE moderation_actions SET status = 'active' WHERE id = ? AND status = 'undoing'",
                (action_id,),
            )

    def complete(self, action_id: int, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE moderation_actions
                SET status = 'undone', undone_at = CURRENT_TIMESTAMP, undone_by = ?
                WHERE id = ? AND status = 'undoing'
                """,
                (user_id, action_id),
            )


class RelayConfigStore:
    """SQLite-backed storage for cross-guild relay mappings."""

    def __init__(self, db_path: str, legacy_path: Path):
        self.db_path = db_path
        self.legacy_path = legacy_path
        self._init()
        self._migrate_legacy()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_links (
                    source_guild_id INTEGER NOT NULL,
                    target_guild_id INTEGER NOT NULL,
                    PRIMARY KEY (source_guild_id, target_guild_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_source_channels (
                    source_guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY (source_guild_id, channel_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_target_channels (
                    source_guild_id INTEGER NOT NULL,
                    target_guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY (source_guild_id, target_guild_id, channel_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_targets_guild "
                "ON relay_target_channels(target_guild_id)"
            )

    def _migrate_legacy(self) -> None:
        if not self.legacy_path.exists():
            return

        try:
            data = json.loads(self.legacy_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError("Relay config is not a dictionary")

            backup_path = self.legacy_path.with_name(
                self.legacy_path.name
                + ".backup-"
                + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            )
            shutil.copy2(self.legacy_path, backup_path)
            self.save(data)
            self.legacy_path.rename(
                self.legacy_path.with_name(self.legacy_path.name + ".migrated")
            )
        except (OSError, TypeError, json.JSONDecodeError):
            logger.exception(
                "Failed to migrate legacy relay configuration from %s",
                self.legacy_path,
            )
            return

    def load(self) -> dict:
        with self._connect() as connection:
            links = connection.execute(
                "SELECT source_guild_id, target_guild_id FROM relay_links"
            ).fetchall()
            sources = connection.execute(
                "SELECT source_guild_id, channel_id FROM relay_source_channels"
            ).fetchall()
            targets = connection.execute(
                """
                SELECT target_guild_id, channel_id
                FROM relay_target_channels
                """
            ).fetchall()

        links_by_source: dict[int, list[int]] = {}
        for row in links:
            links_by_source.setdefault(row["source_guild_id"], []).append(
                row["target_guild_id"]
            )

        return {
            "links": [
                {
                    "source_guild": source_id,
                    "target_guilds": target_ids,
                }
                for source_id, target_ids in links_by_source.items()
            ],
            "sources": self._group_channels(sources),
            "targets": self._group_channels(targets, "target_guild_id"),
        }

    @staticmethod
    def _group_channels(
        rows,
        guild_column: str = "source_guild_id",
    ) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for row in rows:
            guild_id = str(row[guild_column])
            grouped.setdefault(guild_id, []).append(str(row["channel_id"]))
        return grouped

    def save(self, data: dict) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM relay_links")
            connection.execute("DELETE FROM relay_source_channels")
            connection.execute("DELETE FROM relay_target_channels")

            for link in data.get("links", []):
                source_id = int(link["source_guild"])
                for target_id in link.get("target_guilds", []):
                    connection.execute(
                        "INSERT OR IGNORE INTO relay_links VALUES (?, ?)",
                        (source_id, int(target_id)),
                    )

            for source_id, channel_ids in data.get("sources", {}).items():
                for channel_id in channel_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO relay_source_channels VALUES (?, ?)",
                        (int(source_id), int(channel_id)),
                    )

            source_guilds_by_target = {}
            target_guilds_by_source = {}
            for link in data.get("links", []):
                source_id = int(link["source_guild"])
                target_ids = {
                    int(target_id) for target_id in link.get("target_guilds", [])
                }
                target_guilds_by_source[source_id] = target_ids
                for target_id in target_ids:
                    source_guilds_by_target.setdefault(int(target_id), set()).add(
                        source_id
                    )

            for target_id, channel_ids in data.get("targets", {}).items():
                target_id_int = int(target_id)
                source_ids = source_guilds_by_target.get(target_id_int)

                # Legacy data keyed channels by source guild. Fan those
                # channels out to every target in that source's link.
                if source_ids is None:
                    target_ids = target_guilds_by_source.get(target_id_int)
                    if target_ids is not None:
                        for linked_target_id in target_ids:
                            for channel_id in channel_ids:
                                connection.execute(
                                    "INSERT OR IGNORE INTO relay_target_channels "
                                    "VALUES (?, ?, ?)",
                                    (target_id_int, linked_target_id, int(channel_id)),
                                )
                        continue

                source_ids = source_ids or set()

                for channel_id in channel_ids:
                    for source_id in source_ids:
                        connection.execute(
                            "INSERT OR IGNORE INTO relay_target_channels VALUES (?, ?, ?)",
                            (source_id, target_id_int, int(channel_id)),
                        )