"""Every entrypoint sets logging up through services.core.logging_utils, and service code logs
through a logger rather than print, so output carries a timestamp, level and source."""
import ast
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from tests import ROOT_DIR


SOURCE_ROOTS = ("Python Scripts", "Data", "api_support", "api_routes", "scripts")
LOGGING_UTILS = Path("Python Scripts/services/core/logging_utils.py")
# Long-running service and library modules: their output goes to the logs, not to stdout.
SERVICE_MODULES = (
    "Python Scripts/agentCallers/agent_pipeline/news_refresh.py",
    "Python Scripts/Webscraping/pipelines/companyNewsPipeline.py",
    "Python Scripts/Webscraping/pipelines/industryNewsPipeline.py",
    "Python Scripts/Webscraping/pipelines/macroNewsPipeline.py",
    "Python Scripts/Webscraping/pipelines/sectorNewsPipeline.py",
    "Python Scripts/Webscraping/pipelines/_regionNewsPipeline.py",
    "Python Scripts/Webscraping/engine/article_scraper.py",
    "Python Scripts/services/runtime/worker_application.py",
    "Python Scripts/trading_support/account.py",
    "Python Scripts/WritingToDataBase/stringtoJson.py",
    "Python Scripts/Webscraping/market_data/yFinanceData.py",
    "Python Scripts/Webscraping/market_data/yFinanceRealTime.py",
    "Python Scripts/agentCallers/agent_runtime/market_health.py",
    "Python Scripts/agentCallers/agent_runtime/researcher.py",
)


def _is_main_guard(node: ast.AST) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def _calls(tree: ast.AST, skip_main_guard: bool = False):
    def visit(node):
        if skip_main_guard and _is_main_guard(node):
            return
        if isinstance(node, ast.Call):
            yield node
        for child in ast.iter_child_nodes(node):
            yield from visit(child)
    yield from visit(tree)


def _source_paths():
    for root in SOURCE_ROOTS:
        yield from (path for path in (ROOT_DIR / root).rglob("*.py") if "__pycache__" not in path.parts)


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger()
        saved = (root.handlers[:], root.level)
        self.addCleanup(lambda: (setattr(root, "handlers", saved[0]), root.setLevel(saved[1])))

    def test_console_logging_without_a_prefix(self):
        from services.core.logging_utils import configure_logging

        self.assertIsNone(configure_logging())

        handlers = logging.getLogger().handlers
        self.assertEqual([logging.StreamHandler], [type(handler) for handler in handlers])
        self.assertEqual(logging.INFO, logging.getLogger().level)

    def test_a_prefix_also_writes_a_timestamped_log_file(self):
        from services.core import logging_utils

        with TemporaryDirectory() as logs_dir, mock.patch.object(logging_utils, "LOGS_DIR", Path(logs_dir)):
            log_path = logging_utils.configure_logging("news_collector")
            logging.getLogger("tests.logging").info("hello")
            for handler in logging.getLogger().handlers:
                handler.flush()

            self.assertEqual(Path(logs_dir), log_path.parent)
            self.assertTrue(log_path.name.startswith("news_collector_"))
            self.assertIn("[INFO] tests.logging: hello", log_path.read_text(encoding="utf-8"))
            for handler in logging.getLogger().handlers:
                handler.close()

    def test_only_logging_utils_calls_basic_config(self):
        offenders = []
        for path in _source_paths():
            if path.relative_to(ROOT_DIR) == LOGGING_UTILS:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in _calls(tree):
                if isinstance(call.func, ast.Attribute) and call.func.attr == "basicConfig":
                    offenders.append(f"{path.relative_to(ROOT_DIR)}:{call.lineno}")
        self.assertEqual([], offenders)

    def test_service_modules_log_instead_of_printing(self):
        offenders = []
        for name in SERVICE_MODULES:
            path = ROOT_DIR / name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in _calls(tree, skip_main_guard=True):
                if isinstance(call.func, ast.Name) and call.func.id == "print":
                    offenders.append(f"{name}:{call.lineno}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
