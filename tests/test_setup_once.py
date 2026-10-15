from __future__ import annotations

from contextlib import closing
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db_common
from db_common import forget_setup, get_connection, use_database
from market_db_modules import bootstrap as market_bootstrap
from news_db_modules import bootstrap as news_bootstrap


class SetupOnceTests(unittest.TestCase):
    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db_path = Path(temp_dir.name) / "once.sqlite3"
        self.addCleanup(forget_setup, self.db_path)

    def assert_opens_no_connection(self, bootstrap, *args, **kwargs):
        boom = AssertionError("opened a connection")
        with patch.object(bootstrap, "get_connection", side_effect=boom), \
                patch.object(db_common, "get_connection", side_effect=boom):
            bootstrap.initialize_database(*args, **kwargs)

    def tables(self):
        with closing(get_connection(self.db_path)) as conn:
            return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def test_second_market_initialize_opens_no_connection(self):
        market_bootstrap.initialize_database(self.db_path)
        self.assert_opens_no_connection(market_bootstrap, self.db_path)

    def test_second_news_initialize_opens_no_connection(self):
        market_bootstrap.initialize_database(self.db_path)
        news_bootstrap.initialize_database(self.db_path)
        self.assert_opens_no_connection(news_bootstrap, self.db_path)

    def test_file_replaced_by_rename_is_set_up_again(self):
        market_bootstrap.initialize_database(self.db_path)
        replacement = self.db_path.with_name("replacement.sqlite3")
        sqlite3.connect(replacement).close()
        os.replace(replacement, self.db_path)
        for suffix in ("-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)

        market_bootstrap.initialize_database(self.db_path)

        self.assertIn("schema_bootstrap", self.tables())

    def test_deleted_file_is_set_up_again(self):
        market_bootstrap.initialize_database(self.db_path)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)

        market_bootstrap.initialize_database(self.db_path)

        self.assertIn("schema_bootstrap", self.tables())

    def test_forget_setup_rechecks_the_database(self):
        market_bootstrap.initialize_database(self.db_path)
        forget_setup(self.db_path)
        with patch.object(market_bootstrap, "get_connection", wraps=get_connection) as opened:
            market_bootstrap.initialize_database(self.db_path)
        opened.assert_called()

    def test_market_setup_does_not_mark_news_current(self):
        market_bootstrap.initialize_database(self.db_path)
        news_bootstrap.initialize_database(self.db_path)
        self.assertIn("news_articles", self.tables())

    def test_use_database_and_explicit_path_share_the_memo(self):
        with use_database(self.db_path):
            market_bootstrap.initialize_database()
        self.assert_opens_no_connection(market_bootstrap, self.db_path)


if __name__ == "__main__":
    unittest.main()
