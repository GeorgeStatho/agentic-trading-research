from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

import db_common
from db_common import (
    Database,
    current_database,
    database_path,
    get_connection,
    use_database,
)


class DatabaseHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_current_database_is_the_env_path(self) -> None:
        self.assertEqual(current_database().path, db_common.DB_PATH)

    def test_use_database_swaps_nests_and_restores_after_an_exception(self) -> None:
        outer, inner = self.tmp / "outer.db", self.tmp / "inner.db"
        with use_database(outer) as db:
            self.assertEqual(db, Database(outer))
            self.assertEqual(current_database().path, outer)
            with use_database(str(inner)):
                self.assertEqual(current_database().path, inner)
            self.assertEqual(current_database().path, outer)
            with self.assertRaises(RuntimeError):
                with use_database(inner):
                    raise RuntimeError
            self.assertEqual(current_database().path, outer)
        self.assertEqual(current_database().path, db_common.DB_PATH)

    def test_get_connection_without_a_path_opens_the_current_database(self) -> None:
        target = self.tmp / "x.db"
        with use_database(target):
            conn = get_connection()
            self.addCleanup(conn.close)
            self.assertTrue(target.exists())
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertIs(conn.row_factory, sqlite3.Row)
            self.assertEqual(conn.isolation_level, "IMMEDIATE")

    def test_explicit_path_wins_inside_use_database(self) -> None:
        explicit = self.tmp / "explicit.db"
        with use_database(self.tmp / "current.db"):
            get_connection(explicit).close()
        self.assertTrue(explicit.exists())
        self.assertFalse((self.tmp / "current.db").exists())

    def test_other_threads_see_the_process_default(self) -> None:
        seen = []
        with use_database(self.tmp / "x.db"):
            t = threading.Thread(target=lambda: seen.append(current_database().path))
            t.start()
            t.join()
        self.assertEqual(seen, [db_common.DB_PATH])

    def test_database_connect_uses_its_path(self) -> None:
        target = self.tmp / "c.db"
        Database(target).connect().close()
        self.assertTrue(target.exists())

    def test_database_path_resolves_none_and_passes_explicit_values_through(self) -> None:
        explicit = self.tmp / "e.db"
        self.assertIs(database_path(explicit), explicit)
        self.assertEqual(database_path("a.db"), "a.db")
        with use_database(self.tmp / "cur.db"):
            self.assertEqual(database_path(None), self.tmp / "cur.db")
            self.assertEqual(database_path(), self.tmp / "cur.db")


if __name__ == "__main__":
    unittest.main()
