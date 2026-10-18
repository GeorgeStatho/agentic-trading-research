from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.marketdata_budget import MarketDataBudget
from market_db_modules.marketdata_sync import EodSyncSettings, run_due_eod_sync


class EodSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "sync.db"
        initialize_database(db_path=self.db_path)
        env = patch.dict("os.environ", {"MARKETDATA_TOKEN": "test-token", "MARKETDATA_DAILY_CREDIT_BUDGET": "100"})
        env.start()
        self.addCleanup(env.stop)
        self.now = datetime.fromisoformat("2026-09-25T14:00:00+00:00")
        self.fetcher = Mock(return_value={"fetch_run_id": 123, "status": "completed", "inserted_quote_count": 2})

    def run_sync(self, *, now=None, settings=None, fetcher=None):
        return run_due_eod_sync(settings or EodSyncSettings(), db_path=self.db_path,
                                now=now or self.now, fetcher=fetcher or self.fetcher)

    def test_once_per_day_survives_new_callers_and_concurrent_starts(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.run_sync(), range(4)))
        self.assertEqual(sum(result["status"] == "completed" for result in results), 1)
        self.assertEqual(self.run_sync()["status"], "already_ran")
        self.fetcher.assert_called_once()
        self.run_sync(now=datetime.fromisoformat("2026-09-26T14:00:00+00:00"))
        self.assertEqual(self.fetcher.call_count, 2)

    def test_failure_is_recorded_and_not_repeated_until_next_day(self):
        self.fetcher.side_effect = RuntimeError("test failure")
        with self.assertRaises(RuntimeError):
            self.run_sync()
        self.assertEqual(self.run_sync()["status"], "already_ran")
        self.fetcher.assert_called_once()
        with closing(get_connection(self.db_path)) as conn:
            row = conn.execute("SELECT status, error_text FROM marketdata_eod_sync_runs").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("test failure", row["error_text"])

    def test_automatic_fetch_respects_allowance_used_by_manual_fetches(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("""INSERT INTO manager_decision_history
                         (symbol, decision_run_at, manager_decision, selected_option_symbol)
                         VALUES ('MSFT', '2026-01-01T10:00:00+00:00', 'call', 'MSFT260117C00400000')""")
        with patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": "1"}):
            MarketDataBudget(self.db_path).reserve()
            with patch("market_db_modules.backtest_marketdata.requests") as client:
                result = run_due_eod_sync(EodSyncSettings(), db_path=self.db_path, now=self.now)
                client.get.assert_not_called()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["stop_reason"], "daily_credit_budget_exhausted")


if __name__ == "__main__":
    unittest.main()
