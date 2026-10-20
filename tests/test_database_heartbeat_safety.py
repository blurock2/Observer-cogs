"""Regression tests for Discord heartbeat stalls caused by SQLite I/O."""
import asyncio
import sqlite3
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs import leveling
from cogs.leveling import Leveling
from cogs.server_stats import ServerInfo
from database import connect_sqlite


def pending_cog():
    cog = object.__new__(Leveling)
    cog._db_lock = asyncio.Lock()
    cog._user_state = {(1, 2): (25, 0, 1)}
    cog._pending_xp = {(1, 2): (25, 1)}
    cog._pending_weekly_xp = {(1, 2, "2026-10-19"): 25}
    cog._xp_flush_task = None
    return cog


class XPFlushTests(unittest.IsolatedAsyncioTestCase):
    async def test_flush_keeps_event_loop_responsive(self):
        cog = pending_cog()
        started, release = threading.Event(), threading.Event()

        def blocked_worker(*args):
            started.set()
            if not release.wait(3):
                raise TimeoutError("worker not released")

        with patch.object(Leveling, "_write_xp_batch", staticmethod(blocked_worker)):
            flush = asyncio.create_task(cog._flush_pending_xp())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                # The worker is still blocked but asyncio tasks should run.
                await asyncio.wait_for(asyncio.sleep(0.01), 0.5)
                self.assertFalse(flush.done())
                self.assertTrue(cog._pending_xp)
            finally:
                release.set()
            await asyncio.wait_for(flush, 3)
        self.assertFalse(cog._pending_xp)
        self.assertFalse(cog._pending_weekly_xp)

    async def test_failed_flush_keeps_xp_for_retry(self):
        cog = pending_cog()
        with patch.object(
            Leveling, "_write_xp_batch",
            staticmethod(lambda *_: (_ for _ in ()).throw(sqlite3.OperationalError("locked"))),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                await cog._flush_pending_xp()
        self.assertEqual(cog._pending_xp[(1, 2)], (25, 1))
        self.assertEqual(cog._pending_weekly_xp[(1, 2, "2026-10-19")], 25)

    async def test_retry_flushes_exactly_one_batch(self):
        cog = pending_cog()
        attempts = []

        def sometimes_fails(*args):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("locked")

        with (
            patch.object(Leveling, "_write_xp_batch", staticmethod(sometimes_fails)),
            patch.object(leveling, "XP_FLUSH_DELAY_SECONDS", 0.001),
        ):
            await asyncio.wait_for(cog._delayed_xp_flush(), 2)
        self.assertEqual(len(attempts), 2)
        self.assertFalse(cog._pending_xp)

    async def test_cancelled_flush_waits_for_commit(self):
        cog = pending_cog()
        started, release = threading.Event(), threading.Event()

        def blocked_worker(*args):
            started.set()
            if not release.wait(3):
                raise TimeoutError("worker not released")

        with patch.object(Leveling, "_write_xp_batch", staticmethod(blocked_worker)):
            flush = asyncio.create_task(cog._flush_pending_xp())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                flush.cancel()
                await asyncio.sleep(0)
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(flush, 3)
        self.assertFalse(cog._pending_xp)


class CounterThrottleTests(unittest.IsolatedAsyncioTestCase):
    async def test_renames_are_rate_limited(self):
        cog = object.__new__(ServerInfo)
        cog._counter_next_edit_at = {}
        calls = []

        class Channel:
            id = 11
            name = "Old"

            async def edit(self, **kwargs):
                calls.append(kwargs)
                self.name = kwargs["name"]

        channel = Channel()
        await cog._rename_counter(channel, "Total: 100")
        channel.name = "Old"  # simulate a subsequent external change
        await cog._rename_counter(channel, "Total: 101")
        self.assertEqual(len(calls), 1)


class DatabaseBackupTests(unittest.TestCase):
    def test_sqlite_backup_reads_committed_wal_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.db"
            with patch.object(leveling, "DATABASE_PATH", path):
                leveling._init_db()
                conn = connect_sqlite(path)
                try:
                    conn.execute(
                        "INSERT INTO users (guild_id,user_id,xp,level,messages) "
                        "VALUES (1,2,25,0,1)"
                    )
                    conn.commit()
                finally:
                    conn.close()
                backup = leveling._backup_database()
                self.assertIsNotNone(backup)
                with sqlite3.connect(backup) as db:
                    self.assertEqual(
                        db.execute("SELECT xp FROM users WHERE guild_id=1 AND user_id=2")
                          .fetchone()[0],
                        25,
                    )


if __name__ == "__main__":
    unittest.main()
