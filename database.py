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
logger = logging.getLogger("aquila.database")

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


def migrate_old_primary_database(primary_path: str | Path) -> None:
    """One-time migration from the old root-level bot database."""
    primary = Path(primary_path)
    legacy = BASE_DIR.parent / "bot.20260918_144801.db"
    migration_name = "root_bot_db_to_primary_v1"

    if not legacy.exists() or legacy.resolve() == primary.resolve():
        return

    with closing(connect_sqlite(primary, timeout=30)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS database_migrations (
                name TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        done = connection.execute(
            "SELECT 1 FROM database_migrations WHERE name = ?",
            (migration_name,),
        ).fetchone()
        if done:
            return

        backup = primary.with_name(
            f"{primary.stem}.before-root-db-migration-"
            f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.db"
        )
        shutil.copy2(primary, backup)

        connection.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))

        try:
            legacy_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM legacy.sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

            shared_tables = (
                "account_links",
                "guild_config",
                "guild_rules",
                "message_stats",
                "moderation_actions",
                "moderation_stats",
                "relay_links",
                "relay_source_channels",
                "relay_target_channels",
                "role_rewards",
                "users",
                "weekly_posts",
                "weekly_xp",
            )

            for table in shared_tables:
                if table not in legacy_tables:
                    continue

                destination_columns = [
                    row["name"]
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                ]
                source_columns = {
                    row["name"]
                    for row in connection.execute(
                        f"PRAGMA legacy.table_info({table})"
                    ).fetchall()
                }

                columns = [column for column in destination_columns if column in source_columns]
                if not columns:
                    continue

                quoted = ", ".join(f'"{column}"' for column in columns)
                connection.execute(
                    f'INSERT OR IGNORE INTO "{table}" ({quoted}) '
                    f'SELECT {quoted} FROM legacy."{table}"'
                )

            connection.execute(
                "INSERT INTO database_migrations (name) VALUES (?)",
                (migration_name,),
            )
            connection.commit()

        finally:
            connection.execute("DETACH DATABASE legacy")

    logger.info("Migrated old primary database %s into %s", legacy, primary)


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


class ModerationCaseStore:
    """Persistent moderation cases, notes, evidence, and report metadata."""

    def __init__(self, db_path: str | Path = DATABASE_PATH):
        self.db_path = str(db_path)
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS moderation_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    case_number INTEGER NOT NULL,
                    case_type TEXT NOT NULL,
                    target_id INTEGER NOT NULL,
                    moderator_id INTEGER,
                    reason TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    reporter_id INTEGER,
                    source_message_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TEXT,
                    UNIQUE (guild_id, case_number)
                );
                CREATE INDEX IF NOT EXISTS idx_cases_target
                    ON moderation_cases(guild_id, target_id, created_at);
                CREATE TABLE IF NOT EXISTS staff_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evidence_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    case_id INTEGER,
                    message_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    content TEXT,
                    attachments TEXT,
                    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS user_name_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, user_id, name)
                );
                """
            )

    def create_case(
        self,
        guild_id: int,
        case_type: str,
        target_id: int,
        moderator_id: int | None = None,
        reason: str | None = None,
        *,
        reporter_id: int | None = None,
        source_message_id: int | None = None,
        status: str = "open",
    ) -> int:
        with self._connect() as connection:
            next_number = connection.execute(
                "SELECT COALESCE(MAX(case_number), 0) + 1 FROM moderation_cases "
                "WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()[0]
            cursor = connection.execute(
                """
                INSERT INTO moderation_cases
                    (guild_id, case_number, case_type, target_id, moderator_id,
                     reason, status, reporter_id, source_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id, next_number, case_type, target_id, moderator_id,
                    reason, status, reporter_id, source_message_id,
                ),
            )
            return int(cursor.lastrowid)

    def get_case(self, case_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM moderation_cases WHERE id = ?", (case_id,)
            ).fetchone()

    def list_user_history(self, guild_id: int, user_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM moderation_cases WHERE guild_id = ? AND target_id = "
                "? ORDER BY case_number DESC",
                (guild_id, user_id),
            ).fetchall()

    def add_note(self, guild_id: int, target_id: int, author_id: int, note: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO staff_notes (guild_id, target_id, author_id, note) "
                "VALUES (?, ?, ?, ?)",
                (guild_id, target_id, author_id, note),
            )
            return int(cursor.lastrowid)

    def list_notes(self, guild_id: int, target_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM staff_notes WHERE guild_id = ? AND target_id = ? "
                "ORDER BY id DESC",
                (guild_id, target_id),
            ).fetchall()

    def add_name(self, guild_id: int, user_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO user_name_history (guild_id, user_id, name) "
                "VALUES (?, ?, ?)",
                (guild_id, user_id, name),
            )

    def list_names(self, guild_id: int, user_id: int) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name FROM user_name_history WHERE guild_id = ? AND user_id = ? "
                "ORDER BY id DESC",
                (guild_id, user_id),
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def add_evidence(
        self,
        guild_id: int,
        message_id: int,
        author_id: int,
        channel_id: int,
        content: str,
        attachments: str = "",
        case_id: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO evidence_snapshots
                    (guild_id, case_id, message_id, author_id, channel_id,
                     content, attachments)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, case_id, message_id, author_id, channel_id, content, attachments),
            )

    def find_open_report(self, guild_id: int, target_id: int, message_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM moderation_cases WHERE guild_id = ? AND case_type = 'report' "
                "AND target_id = ? AND source_message_id = ? AND status = 'open'",
                (guild_id, target_id, message_id),
            ).fetchone()

    def update_case_status(self, case_id: int, status: str, moderator_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE moderation_cases SET status = ?, moderator_id = ?, "
                "resolved_at = CASE WHEN ? IN ('resolved', 'closed') "
                "THEN CURRENT_TIMESTAMP ELSE resolved_at END WHERE id = ?",
                (status, moderator_id, status, case_id),
            )

    def get_evidence(self, guild_id: int, message_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM evidence_snapshots WHERE guild_id = ? AND message_id = ?",
                (guild_id, message_id),
            ).fetchone()

    def staff_summary(self, guild_id: int, moderator_id: int) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS handled, "
                "SUM(CASE WHEN status IN ('closed', 'resolved') AND moderator_id = ? THEN 1 ELSE 0 END) AS resolved, "
                "SUM(CASE WHEN status = 'open' AND moderator_id = ? THEN 1 ELSE 0 END) AS open_assigned, "
                "AVG(CASE WHEN status IN ('closed', 'resolved') "
                "THEN (julianday(resolved_at) - julianday(created_at)) * 1440 END) AS response_minutes "
                "FROM moderation_cases WHERE guild_id = ? AND moderator_id = ?",
                (moderator_id, moderator_id, guild_id, moderator_id),
            ).fetchone()
            open_reports = connection.execute(
                "SELECT COUNT(*) FROM moderation_cases WHERE guild_id = ? "
                "AND case_type = 'report' AND status = 'open'",
                (guild_id,),
            ).fetchone()[0]
        return {
            "handled": int(row["resolved"] or 0),
            "open_assigned": int(row["open_assigned"] or 0),
            "open_reports": int(open_reports),
            "response_minutes": round(float(row["response_minutes"] or 0), 1),
        }


class TagStore:
    """Guild-scoped plain-text reusable moderation and support messages."""

    def __init__(self, db_path: str | Path = DATABASE_PATH):
        self.db_path = str(db_path)
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
                CREATE TABLE IF NOT EXISTS moderation_tags (
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    updated_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, name)
                )
                """
            )

    @staticmethod
    def normalize_name(name: str) -> str:
        return " ".join(name.strip().lower().split())

    def create(self, guild_id: int, name: str, content: str, user_id: int) -> bool:
        name = self.normalize_name(name)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO moderation_tags "
                "(guild_id, name, content, created_by, updated_by) VALUES (?, ?, ?, ?, ?)",
                (guild_id, name, content, user_id, user_id),
            )
            return cursor.rowcount == 1

    def get(self, guild_id: int, name: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM moderation_tags WHERE guild_id = ? AND name = ?",
                (guild_id, self.normalize_name(name)),
            ).fetchone()

    def update(self, guild_id: int, name: str, content: str, user_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE moderation_tags SET content = ?, updated_by = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE guild_id = ? AND name = ?",
                (content, user_id, guild_id, self.normalize_name(name)),
            )
            return cursor.rowcount == 1

    def delete(self, guild_id: int, name: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM moderation_tags WHERE guild_id = ? AND name = ?",
                (guild_id, self.normalize_name(name)),
            )
            return cursor.rowcount == 1

    def list(self, guild_id: int) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name FROM moderation_tags WHERE guild_id = ? ORDER BY name",
                (guild_id,),
            ).fetchall()
        return [str(row["name"]) for row in rows]


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