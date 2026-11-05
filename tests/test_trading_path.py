"""Research evidence is bound only to the files that can change a trading decision."""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from services.core import trading_path
from services.core.trading_path import ROOT, import_closure, trading_path_files


def write(root: Path, name: str, text: str = '') -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class ImportClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root, 'Python Scripts/agentCallers/entry.py',
              'import json\nfrom services.gates import gate\n\ndef run():\n    from services.lazy import thing\n')
        write(self.root, 'Python Scripts/services/__init__.py')
        write(self.root, 'Python Scripts/services/gates/__init__.py')
        write(self.root, 'Python Scripts/services/gates/gate.py', 'from .shared import helper\n')
        write(self.root, 'Python Scripts/services/gates/shared.py', 'from market_db_modules import store\n')
        write(self.root, 'Python Scripts/services/lazy.py')
        write(self.root, 'Data/market_db_modules/__init__.py')
        write(self.root, 'Data/market_db_modules/store.py')
        write(self.root, 'Python Scripts/services/dashboard_only.py', 'from services.gates import gate\n')

    def tearDown(self):
        self.tmp.cleanup()

    def closure(self):
        return {str(path.relative_to(self.root)) for path in import_closure(['entry'], root=self.root)}

    def test_follows_absolute_relative_and_function_level_imports(self):
        self.assertEqual(self.closure(), {
            'Python Scripts/agentCallers/entry.py',
            'Python Scripts/services/__init__.py',
            'Python Scripts/services/gates/__init__.py',
            'Python Scripts/services/gates/gate.py',
            'Python Scripts/services/gates/shared.py',
            'Python Scripts/services/lazy.py',
            'Data/market_db_modules/__init__.py',
            'Data/market_db_modules/store.py',
        })

    def test_a_module_nothing_on_the_path_imports_is_left_out(self):
        self.assertNotIn('Python Scripts/services/dashboard_only.py', self.closure())

    def test_an_unknown_entry_module_is_an_error(self):
        with self.assertRaises(ValueError):
            import_closure(['missing'], root=self.root)

    def test_hash_changes_only_when_a_trading_path_file_changes(self):
        files = tuple(sorted(self.closure()))
        with patch.object(trading_path, 'ROOT', self.root), \
                patch.object(trading_path, 'trading_path_files', return_value=files):
            before = trading_path.trading_path_hashes()
            write(self.root, 'Python Scripts/services/dashboard_only.py', '# reworded\n')
            self.assertEqual(trading_path.trading_path_hashes(), before)
            write(self.root, 'Python Scripts/services/gates/shared.py', 'THRESHOLD = 2\n')
            self.assertNotEqual(trading_path.trading_path_hashes(), before)


class RepositoryTradingPathTests(unittest.TestCase):
    def test_covers_decision_order_exit_and_gate_input_code(self):
        files = set(trading_path_files())
        for path in ('Python Scripts/agentCallers/decision_pipeline.py',
                     'Python Scripts/agentCallers/agent_stages/manager_runner.py',
                     'Python Scripts/services/gates/trade_economics.py',
                     'Python Scripts/services/trading/trade_executor.py',
                     'Python Scripts/services/options/position_manager.py',
                     'Python Scripts/services/discovery/catalyst_classification.py',
                     'Data/news_db_modules/catalyst_labels.py',
                     'Data/catalyst_models.py',
                     'config/risk_clusters.csv'):
            self.assertIn(path, files)

    def test_covers_every_schema_bootstrap_runs_and_the_code_that_picks_discovery_companies(self):
        files = set(trading_path_files())
        schemas = {str(path.relative_to(ROOT)) for pattern in ('Data/*.sql', 'Data/market_db_modules/*.sql')
                   for path in ROOT.glob(pattern)}
        self.assertLessEqual({'Data/market_db_modules/discovery_schema.sql',
                              'Data/market_db_modules/account_positions_schema.sql'}, schemas)
        self.assertLessEqual(schemas, files)
        for path in ('Python Scripts/services/discovery/market_events.py',
                     'Python Scripts/services/discovery/company_evidence_service.py',
                     'Python Scripts/Webscraping/company_evidence/sec_current.py'):
            self.assertIn(path, files)

    def test_is_cheap_enough_to_build_at_worker_start(self):
        # The old folder-wide hash read every file in a few seconds; following imports must not cost more.
        started = time.monotonic()
        import_closure(trading_path.ENTRY_MODULES)
        self.assertLess(time.monotonic() - started, 10)

    def test_leaves_out_code_that_cannot_change_a_decision(self):
        files = set(trading_path_files())
        for path in ('api_support/v2_status.py', 'scripts/research.py', 'Python Scripts/db_backup_main.py'):
            self.assertNotIn(path, files)
        self.assertFalse([path for path in files if path.startswith(('web_dashboard/', 'tests/'))])


if __name__ == '__main__':
    unittest.main()
