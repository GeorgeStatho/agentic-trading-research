from __future__ import annotations

from contextlib import closing
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database


class MarketSetupFingerprintTests(unittest.TestCase):
    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db_path = Path(temp_dir.name) / "market.sqlite3"
        initialize_database(db_path=self.db_path)
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("ALTER TABLE manager_decision_history DROP COLUMN exit_reason")

    def manager_history_columns(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            return {row["name"] for row in conn.execute("PRAGMA table_info(manager_decision_history)")}

    def test_database_set_up_by_the_same_files_is_left_alone(self):
        initialize_database(db_path=self.db_path)

        self.assertNotIn("exit_reason", self.manager_history_columns())

    def test_database_set_up_by_other_files_is_migrated(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE schema_bootstrap SET fingerprint = 'older-setup'")

        initialize_database(db_path=self.db_path)

        self.assertIn("exit_reason", self.manager_history_columns())


if __name__ == "__main__":
    unittest.main()
