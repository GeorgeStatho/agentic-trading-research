"""SQLite plans from table statistics only once ANALYZE has written sqlite_stat1."""
import ast
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import ROOT_DIR


class PlannerStatisticsTests(unittest.TestCase):
    def test_analyze_database_writes_statistics(self):
        from db_common import analyze_database

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "stats.db"
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("CREATE TABLE quotes (symbol TEXT, at TEXT)")
                conn.execute("CREATE INDEX idx_quotes_symbol ON quotes(symbol)")
                conn.executemany("INSERT INTO quotes VALUES (?, ?)", [("A", "1"), ("B", "2")])
            analyze_database(db)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute("SELECT idx FROM sqlite_stat1 WHERE tbl='quotes'").fetchall()
        self.assertIn(("idx_quotes_symbol",), rows)

    def test_api_startup_analyzes_the_database(self):
        tree = ast.parse((ROOT_DIR / "api.py").read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.If)
                    and "__main__" in ast.unparse(node.test) and "app.run" in ast.unparse(node))
        calls = {ast.unparse(node.func) for node in ast.walk(main) if isinstance(node, ast.Call)}
        self.assertIn("analyze_database", calls)


if __name__ == "__main__":
    unittest.main()
