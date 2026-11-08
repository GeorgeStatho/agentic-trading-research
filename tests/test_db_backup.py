from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, time as clock_time, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from services.runtime import db_backup
from services.runtime.db_backup import (LATEST_NAME, MANIFEST_NAME, DbBackupSettings, backup_database,
                                        backup_problem, due_schedule_date, read_manifest, replica_lag_seconds)


class DbBackupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "db" / "stock_experiment.db"
        self.db.parent.mkdir()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
            conn.executemany("INSERT INTO trades (symbol) VALUES (?)", [("AAA",), ("BBB",)])
        self.settings = DbBackupSettings(
            db_path=self.db, backup_dir=self.root / "backups", temp_dir=self.root / "db" / ".backup-tmp",
            replica_dir=self.root / "backups" / "litestream" / "stock_experiment", backup_time=clock_time(5, 0),
            max_replica_lag_seconds=600, max_backup_age_hours=26, retry_seconds=3600)

    def test_backup_is_a_readable_copy_with_a_manifest(self):
        manifest = backup_database(self.settings, schedule_date=date(2026, 11, 4))
        with closing(sqlite3.connect(self.settings.backup_dir / LATEST_NAME)) as copy:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM trades").fetchone()[0], 2)
        self.assertEqual(read_manifest(self.settings.backup_dir), manifest)
        self.assertEqual(manifest["schedule_date"], "2026-11-04")
        self.assertEqual(sorted(path.name for path in self.settings.backup_dir.iterdir()), [LATEST_NAME, MANIFEST_NAME])
        self.assertEqual(list(self.settings.temp_dir.iterdir()), [])

    def test_backup_refreshes_the_live_databases_planner_statistics(self):
        # The nightly backup is the one daily job, so it keeps sqlite_stat1 current for the query planner.
        backup_database(self.settings, schedule_date=date(2026, 11, 4))
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_stat1'").fetchone())

    def test_a_failed_analyze_does_not_fail_the_backup(self):
        with patch.object(db_backup, "analyze_database", side_effect=sqlite3.OperationalError("locked")):
            manifest = backup_database(self.settings, schedule_date=date(2026, 11, 4))
        self.assertEqual(manifest["quick_check"], "ok")

    def test_failed_check_keeps_the_previous_backup(self):
        backup_database(self.settings, schedule_date=date(2026, 11, 4))
        previous = (self.settings.backup_dir / LATEST_NAME).read_bytes()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO trades (symbol) VALUES ('CCC')")
        real_connect = sqlite3.connect

        def failing_check(target, *args, **kwargs):
            conn = real_connect(target, *args, **kwargs)
            # The read-only connection is the one that checks the fresh copy.
            return _QuickCheckFails(conn) if "mode=ro" in str(target) else conn

        with patch.object(db_backup.sqlite3, "connect", side_effect=failing_check):
            with self.assertRaisesRegex(RuntimeError, "quick_check"):
                backup_database(self.settings, schedule_date=date(2026, 11, 5))
        self.assertEqual((self.settings.backup_dir / LATEST_NAME).read_bytes(), previous)
        self.assertEqual(read_manifest(self.settings.backup_dir)["schedule_date"], "2026-11-04")
        self.assertFalse((self.settings.backup_dir / f"{LATEST_NAME}.tmp").exists())

    def test_schedule_runs_once_per_market_day_after_the_backup_time(self):
        before = datetime(2026, 11, 4, 9, 59, tzinfo=timezone.utc)  # 04:59 New York
        after = datetime(2026, 11, 4, 10, 1, tzinfo=timezone.utc)
        self.assertIsNone(due_schedule_date(before, clock_time(5, 0), {}))
        self.assertEqual(due_schedule_date(after, clock_time(5, 0), {}), date(2026, 11, 4))
        self.assertIsNone(due_schedule_date(after, clock_time(5, 0), {"schedule_date": "2026-11-04"}))

    def test_problems_cover_a_stale_backup_and_a_lagging_replica(self):
        now = datetime.now(timezone.utc)
        self.assertIn("No nightly backup", backup_problem(self.settings, {}, now))
        stale = {"completed_at": (now - timedelta(hours=30)).isoformat()}
        self.assertIn("30 hours old", backup_problem(self.settings, stale, now))
        fresh = {"completed_at": now.isoformat()}
        self.assertIn("has not started", backup_problem(self.settings, fresh, now))

        wal = self.settings.replica_dir / "generations" / "abc" / "wal"
        wal.mkdir(parents=True)
        segment = wal / "00000000_00000000.wal.lz4"
        segment.write_bytes(b"x")
        self.assertIsNone(backup_problem(self.settings, fresh, now))
        old = time.time() - 3600
        os.utime(segment, (old, old))
        self.assertGreater(replica_lag_seconds(self.db, self.settings.replica_dir), 3000)
        self.assertIn("minutes behind", backup_problem(self.settings, fresh, now))
        self.assertIsNone(backup_problem(replace(self.settings, replica_dir=None), fresh, now))


class _QuickCheckFails:
    """A connection whose quick_check reports corruption."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA quick_check"):
            return iter([("*** in database main *** Page 3: btree damaged",)])
        return self._conn.execute(sql, *args)

    def close(self):
        self._conn.close()


if __name__ == "__main__":
    unittest.main()
