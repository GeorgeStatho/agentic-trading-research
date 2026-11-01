from __future__ import annotations

import ast
from contextlib import closing
from pathlib import Path
import sqlite3
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import db_common
from db_common import database_path, use_database
from market_db_modules import bootstrap as market_bootstrap
from market_db_modules import entities
from news_db_modules import article_store
from news_db_modules import bootstrap as news_bootstrap
from services.research.research_lock import research_session_lock
from services.research.research_promotion import approved, champion_check, policy_hash


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"
FACADE_MODULES = {"market_db", "news_db", "db", "db_helpers"}
CONTEXT_FACADE_NAMES = {"DB_PATH", "get_connection", "initialize_market_database", "list_option_trade_executions"}
SKIPPED_DIRS = {"graft", ".git", "__pycache__", ".venv", "venv", "env", ".superpowers"}


def _is_db_path(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "DB_PATH" and isinstance(node.value, ast.Name) and node.value.id == "db_common"
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


def _bound_db_path_lines(path: Path) -> list[int]:
    lines = _db_path_defaults(path)
    for node in ast.walk(ast.parse(path.read_bytes())):
        if isinstance(node, ast.Call) and any(_is_db_path(a) for a in node.args + [k.value for k in node.keywords]):
            # db_common's own `DEFAULT_DATABASE = Database(DB_PATH)` is the one binding.
            if not (path.name == "db_common.py" and isinstance(node.func, ast.Name) and node.func.id == "Database"):
                lines.append(node.lineno)
    return lines


def _is_facade(module: str) -> bool:
    return module in FACADE_MODULES or module.startswith("db_helpers.")


def _project_python_files() -> list[Path]:
    return [
        path for path in sorted(ROOT_DIR.rglob("*.py"))
        if not SKIPPED_DIRS & set(path.relative_to(ROOT_DIR).parts)
    ]


def _facade_imports(path: Path) -> list[str]:
    offenders = []
    for node in ast.walk(ast.parse(path.read_bytes())):
        if isinstance(node, ast.Import):
            offenders += [alias.name for alias in node.names if _is_facade(alias.name)]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if _is_facade(node.module):
                offenders.append(node.module)
            elif node.module == "api_support.context":
                offenders += [f"{node.module}.{alias.name}" for alias in node.names if alias.name in CONTEXT_FACADE_NAMES]
    return [f"{path.relative_to(ROOT_DIR)}:{offender}" for offender in offenders]


def _row_count(db_path: Path, table: str) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class PersistenceLayeringTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_db_path_is_only_bound_in_db_common(self) -> None:
        roots = [DATA_DIR, ROOT_DIR / "Python Scripts", ROOT_DIR / "api_support", ROOT_DIR / "scripts"]
        paths = [path for root in roots for path in sorted(root.rglob("*.py"))
                 if "__pycache__" not in path.parts]
        paths += [ROOT_DIR / "api.py", ROOT_DIR / "portfolio_history_service.py"]
        offenders = [f"{path.relative_to(ROOT_DIR)}:{line}" for path in paths for line in _bound_db_path_lines(path)]
        self.assertEqual(offenders, [])

    def test_promotion_checks_read_the_current_database_by_default(self) -> None:
        db_path = self.tmp / "promotion.db"
        with use_database(db_path):
            market_bootstrap.initialize_database()
            policy = {"min_value": 1}
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute("INSERT INTO research_gate_promotions VALUES (?,?,?,?,?,?)",
                             ("LIQUIDITY", policy_hash("LIQUIDITY", policy, "code"), "r", "2026-01-01", 120, 1.0))
            self.assertTrue(approved("LIQUIDITY", policy, code_hash="code"))
            self.assertEqual(champion_check({}), (True, "no_champion", []))

    def test_research_session_lock_defaults_to_the_current_database(self) -> None:
        with use_database(self.tmp / "lock.db"), research_session_lock(None, "x:y"):
            self.assertTrue((self.tmp / ".research-locks" / "x_y").exists())

    def test_empty_db_argument_means_the_current_database(self) -> None:
        from scripts import discovery
        db_path = self.tmp / "script.db"
        with use_database(db_path), patch("sys.argv", ["discovery", "--db", "", "status"]), \
                patch("market_db_modules.discovery_queue.discovery_queue_status",
                      lambda db_path=None: {"db": str(database_path(db_path))}), \
                patch("sys.stdout", new_callable=io.StringIO) as out:
            discovery.main()
        self.assertEqual(json.loads(out.getvalue()), {"db": str(db_path)})

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


    def test_no_module_imports_a_persistence_facade(self) -> None:
        offenders = [offender for path in _project_python_files() for offender in _facade_imports(path)]
        self.assertEqual(offenders, [])

    def test_no_patch_string_targets_a_facade(self) -> None:
        offenders = [
            f"{path.relative_to(ROOT_DIR)}:{node.lineno}"
            for path in sorted((ROOT_DIR / "tests").rglob("*.py"))
            if path != Path(__file__).resolve()  # this file names the prefixes it forbids
            for node in ast.walk(ast.parse(path.read_bytes()))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.startswith(("market_db.", "news_db.", "db_helpers."))
        ]
        self.assertEqual(offenders, [])

    def test_tests_do_not_write_the_session_database(self) -> None:
        modules = ["test_api_backtest_eod_fetch", "test_api_thesis_validator", "test_dashboard_open_positions"]
        own_database = [name for name in modules
                        if not re.search(r"\b(temporary_database|use_database)\(", (ROOT_DIR / "tests" / f"{name}.py").read_text(encoding="utf-8"))]
        self.assertEqual(own_database, [], "these modules do not set up their own database")

        def counts() -> list[int]:
            with closing(sqlite3.connect(db_common.DB_PATH)) as conn:
                found = []
                for table in ("manager_decision_history", "option_trade_executions", "companies"):
                    try:
                        found.append(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    except sqlite3.OperationalError:
                        found.append(0)
                return found

        before = counts()
        suite = unittest.TestLoader().loadTestsFromNames([f"tests.{name}" for name in modules])
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(counts(), before)

    def test_facade_files_are_gone(self) -> None:
        facades = ["market_db.py", "news_db.py", "db.py", "db_helpers", "queryDB.py"]
        self.assertEqual([name for name in facades if (DATA_DIR / name).exists()], [])

    # The Docker build runs the suite without git or .git, so there is nothing to list there.
    @unittest.skipUnless(shutil.which("git") and (ROOT_DIR / ".git").exists(), "needs a git checkout")
    def test_docs_do_not_point_at_facades(self) -> None:
        tracked = subprocess.run(["git", "ls-files", "-z", "*.md"], cwd=ROOT_DIR, capture_output=True, text=True, check=True).stdout
        facade = re.compile(r"market_db\.py|news_db\.py|db_helpers|Data/db\.py|queryDB")  # not market_db_modules
        offenders = [
            f"{name}:{number}"
            for name in tracked.split("\0") if name and not name.startswith(("docs/archive/", "graft/"))
            for number, line in enumerate((ROOT_DIR / name).read_text(encoding="utf-8").splitlines(), 1)
            if facade.search(line)
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
