"""Behavioral coverage for shared transactions and cheaper hot-path operations."""

import sqlite3
from unittest.mock import Mock, patch

import pytest

from cogs.leveling import _level_from_xp, _xp_for_level
from cogs.mod_stats import ModerationStatsStore
from cogs.setup_ui import SetupConfigStore
from database import BanAppealStore, sqlite_transaction


def test_transaction_commits_rolls_back_and_closes(tmp_path):
    path = tmp_path / "bot.db"
    with sqlite_transaction(path) as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")

    with pytest.raises(ValueError, match="abort"):
        with sqlite_transaction(path) as failed:
            failed.execute("INSERT INTO sample VALUES (2)")
            raise ValueError("abort")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        failed.execute("SELECT 1")
    with sqlite_transaction(path) as connection:
        assert [row[0] for row in connection.execute("SELECT value FROM sample")] == [1]


@pytest.mark.parametrize("failure", ["commit", "rollback"])
def test_transaction_closes_even_when_finalization_fails(failure):
    connection = Mock()
    getattr(connection, failure).side_effect = sqlite3.OperationalError("disk failure")
    with patch("database.connect_sqlite", return_value=connection):
        with pytest.raises(sqlite3.OperationalError, match="disk failure"):
            with sqlite_transaction("unused.db"):
                if failure == "rollback":
                    raise ValueError("abort")
    connection.close.assert_called_once()


def test_cached_setting_reads_preserve_defaults_updates_and_copy_isolation(tmp_path):
    path = str(tmp_path / "bot.db")
    store = SetupConfigStore(path)
    other = SetupConfigStore(path)
    assert store.get(1, "example", "missing", 42) == 42
    store.set(1, "example", "enabled", True)
    store.set(1, "example", "nullable", None)
    copied = store.get_module(1, "example")
    copied["enabled"] = False
    # Warm single-key reads must not copy or reload the whole module.
    with patch.object(store, "get_module", side_effect=AssertionError("cache miss")):
        assert store.get(1, "example", "enabled") is True
        assert store.get(1, "example", "nullable", 42) is None
        other.set(1, "example", "enabled", False)
        assert store.get(1, "example", "enabled") is False
        other.delete(1, "example", "enabled")
        assert store.get(1, "example", "enabled", 42) == 42
    other.reset_module(1, "example")
    assert store.get(1, "example", "nullable", 42) == 42
    with sqlite_transaction(path) as connection:
        connection.execute(
            "INSERT INTO guild_setup_config VALUES (?, ?, ?, ?)",
            (1, "example", "enabled", "true"),
        )
    store.refresh_module(1, "example")
    assert store.get(1, "example", "enabled") is True
    assert store.get(2, "example", "enabled") is None


def test_counter_upsert_preserves_other_actions_and_guilds(tmp_path):
    path = tmp_path / "bot.db"
    BanAppealStore(str(path))
    store = ModerationStatsStore(path)
    for action in sorted(store.VALID_ACTIONS):
        store.increment(1, 50, action)
        store.increment(1, 50, action)
    store.increment(2, 50, "bans")
    store.increment(1, 51, "kicks")
    for action in store.VALID_ACTIONS:
        assert store.get(1, 50)[action] == 2
        assert store.get(2, 50)[action] == int(action == "bans")
        assert store.get(1, 51)[action] == int(action == "kicks")
    with pytest.raises(ValueError, match="Unknown moderation stat action"):
        store.increment(1, 50, "invalid")
    assert store.get(1, 50)["bans"] == 2


@pytest.mark.parametrize("level", [1, 2, 10, 999, 10**6, 10**12])
def test_level_thresholds_are_exact(level):
    threshold = _xp_for_level(level)
    assert _level_from_xp(threshold - 1) == level - 1
    assert _level_from_xp(threshold) == level
    assert _level_from_xp(threshold + 1) == level


@pytest.mark.parametrize("xp", [-100, -1, 0, 1, 99])
def test_xp_below_first_level(xp):
    assert _level_from_xp(xp) == 0
