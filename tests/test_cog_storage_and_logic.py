import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cogs.acc_link import AccountLinkStore
from cogs.app_bridge import write_json_atomic
from cogs.leveling import _level_from_xp
from cogs.message_relay import (
    get_source_guilds_for_target,
    get_target_guilds_for_source,
)
from cogs.mod_stats import ModerationStatsStore
from cogs.reaction_roles import ReactionRoles
from cogs.rules import RulesStore
from cogs.setup_ui import SetupConfigStore
from cogs.temporary_voice import TemporaryVoice
from cogs.weather import Weather
from database import cleanup_old_backups, migrate_legacy_databases


class CogStorageAndLogicTests(unittest.TestCase):
    def test_level_boundaries(self):
        cases = {
            0: 0,
            99: 0,
            100: 1,
            399: 1,
            400: 2,
        }

        for xp, expected_level in cases.items():
            with self.subTest(xp=xp):
                self.assertEqual(_level_from_xp(xp), expected_level)

    def test_setup_configuration_isolated_by_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SetupConfigStore(str(Path(directory) / "bot.db"))
            store.set(1, "weather", "enabled", True)
            store.set(2, "weather", "enabled", False)

            self.assertTrue(store.get(1, "weather", "enabled"))
            self.assertFalse(store.get(2, "weather", "enabled"))
            self.assertIsNone(store.get(3, "weather", "enabled"))

    def test_setup_configuration_cache_is_invalidated_on_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SetupConfigStore(str(Path(directory) / "bot.db"))
            store.set(1, "server_stats", "enabled", False)

            self.assertFalse(store.get(1, "server_stats", "enabled"))

            store.set(1, "server_stats", "enabled", True)

            self.assertTrue(store.get(1, "server_stats", "enabled"))

    def test_moderation_stats_are_isolated_by_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ModerationStatsStore(Path(directory) / "bot.db")
            store.increment(1, 50, "bans")
            store.increment(2, 50, "kicks")

            self.assertEqual(store.get(1, 50)["bans"], 1)
            self.assertEqual(store.get(1, 50)["kicks"], 0)
            self.assertEqual(store.get(2, 50)["bans"], 0)
            self.assertEqual(store.get(2, 50)["kicks"], 1)

    def test_rules_are_isolated_by_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RulesStore(Path(directory) / "bot.db")
            rule = store.add(1, "Rules", "Be kind", "embed")

            self.assertIsNotNone(store.get(1, rule.rule_id))
            self.assertIsNone(store.get(2, rule.rule_id))
            self.assertEqual(store.list(2), [])

    def test_account_links_are_isolated_by_user(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountLinkStore(Path(directory) / "accounts.db")
            store.set_link(1, "github", "alice")
            store.set_link(2, "github", "bob")

            self.assertEqual(store.get_link(1, "github"), "alice")
            self.assertEqual(store.get_link(2, "github"), "bob")
            self.assertEqual(store.get_links(3), {})

    def test_leveling_config_check_remains_async_safe(self):
        from cogs.leveling import Leveling

        cog = object.__new__(Leveling)
        cog.store = type("Store", (), {"get_module": lambda self, *_: {}})()
        cog._db_lock = asyncio.Lock()

        async def check():
            config = await cog._get_guild_config(123)
            self.assertEqual(config["enabled"], True)
            self.assertEqual(config["xp_cooldown"], 15)

        asyncio.run(check())

    def test_relay_mappings_are_scoped_to_the_requested_guild(self):
        data = {
            "links": [
                {"source_guild": 1, "target_guilds": [2, 3]},
                {"source_guild": 4, "target_guilds": [5]},
            ],
            "sources": {"1": [10], "4": [40]},
            "targets": {"1": [20], "4": [50]},
        }

        self.assertEqual(get_target_guilds_for_source(data, 1), [2, 3])
        self.assertEqual(get_target_guilds_for_source(data, 4), [5])
        self.assertEqual(get_source_guilds_for_target(data, 3), [1])
        self.assertEqual(get_source_guilds_for_target(data, 5), [4])

    def test_reaction_roles_ignore_invalid_role_ids(self):
        cog = object.__new__(ReactionRoles)
        cog.store = type(
            "Store",
            (),
            {"get": lambda self, *_args, **_kwargs: {"⭐": 10, "bad": "x"}},
        )()

        self.assertEqual(cog._get_roles(1), {"⭐": 10})

    def test_temporary_voice_rooms_are_isolated_by_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            cog = object.__new__(TemporaryVoice)
            cog.store = SetupConfigStore(str(Path(directory) / "bot.db"))
            cog._set_room(1, 100, {"owner_id": 7, "locked": False})

            self.assertIsNotNone(cog._get_room(1, 100))
            self.assertIsNone(cog._get_room(2, 100))

    def test_weather_configuration_normalizes_units_and_location(self):
        cog = object.__new__(Weather)
        values = {
            (1, "units"): "IMPERIAL",
            (1, "location"): "  London  ",
            (2, "units"): "unknown",
            (2, "location"): "   ",
        }
        cog.store = type(
            "Store",
            (),
            {"get": lambda self, guild_id, _module, key, default=None: values.get((guild_id, key), default)},
        )()

        self.assertEqual(cog._units(1), "imperial")
        self.assertEqual(cog._default_location(1), "London")
        self.assertEqual(cog._units(2), "metric")
        self.assertIsNone(cog._default_location(2))

    def test_app_bridge_json_write_replaces_file_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guilds.json"
            write_json_atomic(path, {"guilds": [{"id": 1}]})

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"guilds": [{"id": 1}]},
            )

    def test_database_migration_preserves_rows_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            data_dir.mkdir()
            account_path = data_dir / "account_links.sqlite3"
            with closing(sqlite3.connect(account_path)) as connection:
                connection.execute(
                    "CREATE TABLE account_links ("
                    "user_id INTEGER, platform TEXT, username TEXT, "
                    "updated_at INTEGER, PRIMARY KEY (user_id, platform))"
                )
                connection.execute(
                    "INSERT INTO account_links VALUES (1, 'github', 'alice', 1)"
                )
                connection.commit()

            member_path = data_dir / "member_stats.sqlite3"
            with closing(sqlite3.connect(member_path)) as connection:
                connection.execute(
                    "CREATE TABLE message_stats ("
                    "guild_id INTEGER, user_id INTEGER, message_count INTEGER, "
                    "PRIMARY KEY (guild_id, user_id))"
                )
                connection.execute("INSERT INTO message_stats VALUES (2, 3, 7)")
                connection.commit()

            leveling_path = data_dir / "leveling.db"
            with closing(sqlite3.connect(leveling_path)) as connection:
                connection.execute(
                    "CREATE TABLE users (guild_id INTEGER, user_id INTEGER, "
                    "xp INTEGER, level INTEGER, messages INTEGER, "
                    "PRIMARY KEY (guild_id, user_id))"
                )
                connection.execute("INSERT INTO users VALUES (4, 5, 100, 1, 2)")
                for table, schema in {
                    "weekly_xp": "guild_id INTEGER, user_id INTEGER, week_start TEXT, xp INTEGER",
                    "guild_config": "guild_id INTEGER PRIMARY KEY, level_up_channel INTEGER, level_up_message TEXT, weekly_channel INTEGER, weekly_day INTEGER, weekly_hour INTEGER, weekly_minute INTEGER",
                    "role_rewards": "guild_id INTEGER, level INTEGER, role_id INTEGER",
                    "weekly_posts": "guild_id INTEGER PRIMARY KEY, week_start TEXT",
                }.items():
                    connection.execute(f"CREATE TABLE {table} ({schema})")
                connection.commit()

            primary_path = root / "bot.db"
            with patch("database.BASE_DIR", root):
                migrate_legacy_databases(primary_path)
                migrate_legacy_databases(primary_path)

            with closing(sqlite3.connect(primary_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM account_links").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT message_count FROM message_stats").fetchone()[0],
                    7,
                )
                self.assertEqual(
                    connection.execute("SELECT xp FROM users").fetchone()[0],
                    100,
                )

            self.assertTrue(
                (data_dir / "account_links.sqlite3.pre-migration-backup").exists()
            )

    def test_old_backups_are_cleaned_without_removing_active_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_timestamp = 1

            old_files = (
                root / "leveling_backup_20200101_000000.db",
                root / "account_links.sqlite3.pre-migration-backup",
                root / "message_relay_config.json.backup-20200101000000",
            )
            for backup_path in old_files:
                backup_path.write_text("backup", encoding="utf-8")
                backup_path.touch()
                import os

                os.utime(backup_path, (old_timestamp, old_timestamp))

            old_directory = root / "migration_backup_20200101_000000"
            old_directory.mkdir()
            (old_directory / "account_links.sqlite3").write_text(
                "backup",
                encoding="utf-8",
            )
            import os

            os.utime(old_directory, (old_timestamp, old_timestamp))

            active_database = root / "leveling.db"
            active_database.write_text("active", encoding="utf-8")

            self.assertEqual(cleanup_old_backups(root), 4)
            self.assertTrue(active_database.exists())
            self.assertFalse(any(path.exists() for path in old_files))
            self.assertFalse(old_directory.exists())


if __name__ == "__main__":
    unittest.main()
