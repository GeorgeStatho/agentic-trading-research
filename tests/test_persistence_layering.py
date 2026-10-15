from __future__ import annotations

import ast
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import db_common
from db_common import use_database
from market_db_modules import bootstrap as market_bootstrap
from market_db_modules import entities
from news_db_modules import article_store
from news_db_modules import bootstrap as news_bootstrap


DATA_DIR = Path(__file__).resolve().parents[1] / "Data"


def _is_db_path(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Name) and node.id == "DB_PATH"


def _db_path_defaults(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            defaults = node.args.defaults + node.args.kw_defaults
            lines += [default.lineno for default in defaults if _is_db_path(default)]
        elif isinstance(node, ast.ClassDef):
            for statement in node.body:
                if isinstance(statement, (ast.Assign, ast.AnnAssign)) and _is_db_path(statement.value):
                    lines.append(statement.lineno)
    return lines


def _row_count(db_path: Path, table: str) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class PersistenceLayeringTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_data_layer_has_no_db_path_defaults(self) -> None:
        # db_common's module-level DB_PATH assignment is neither a default nor in a class body.
        offenders = [
            f"{path.relative_to(DATA_DIR)}:{line}"
            for path in sorted(DATA_DIR.rglob("*.py"))
            if "__pycache__" not in path.parts
            for line in _db_path_defaults(path)
        ]
        self.assertEqual(offenders, [])

    def test_store_functions_follow_use_database(self) -> None:
        db_path = self.tmp / "market.db"
        before = _row_count(db_common.DB_PATH, "sectors")
        with use_database(db_path):
            market_bootstrap.initialize_database()
            entities.add_sector("x", "X")
        with closing(sqlite3.connect(db_path)) as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sectors WHERE sector_key = 'x'").fetchone())
        self.assertEqual(_row_count(db_common.DB_PATH, "sectors"), before)

    def test_news_store_follows_use_database(self) -> None:
        db_path = self.tmp / "news.db"
        before = _row_count(db_common.DB_PATH, "news_articles")
        with use_database(db_path):
            news_bootstrap.initialize_database()
            article_store.add_news_article("test", "layering-key", "Title", "https://example.com/a")
        self.assertEqual(_row_count(db_path, "news_articles"), 1)
        self.assertEqual(_row_count(db_common.DB_PATH, "news_articles"), before)

    def test_explicit_db_path_still_wins(self) -> None:
        a, b = self.tmp / "a.db", self.tmp / "b.db"
        market_bootstrap.initialize_database(db_path=b)
        with use_database(a):
            entities.add_sector("x", "X", db_path=b)
        self.assertEqual(_row_count(b, "sectors"), 1)
        self.assertFalse(a.exists())


if __name__ == "__main__":
    unittest.main()
