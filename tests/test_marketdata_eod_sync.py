from __future__ import annotations

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Data"))

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

    def test_schedule_uses_new_york_time_in_summer_and_winter(self):
        for value in ("2026-09-25T13:59:59+00:00", "2026-12-01T14:59:59+00:00"):
            self.assertEqual(self.run_sync(now=datetime.fromisoformat(value))["status"], "not_due")
        self.fetcher.assert_not_called()
        self.assertEqual(self.run_sync(now=datetime.fromisoformat("2026-12-01T15:00:00+00:00"))["status"], "completed")

    def test_disabled_or_missing_token_does_not_claim_or_fetch(self):
        self.assertEqual(self.run_sync(settings=EodSyncSettings(enabled=False))["status"], "disabled")
        with patch.dict("os.environ", {"MARKETDATA_TOKEN": ""}):
            self.assertEqual(self.run_sync()["status"], "missing_token")
        self.fetcher.assert_not_called()
        self.assertEqual(self.run_sync()["status"], "completed")

    def test_fetches_mature_windows_with_retry_delay_and_saves_audit(self):
        result = self.run_sync()
        args = self.fetcher.call_args.kwargs
        self.assertEqual(args["end_at"], "2026-09-16T14:00:00+00:00")
        self.assertEqual(args["retry_before_at"], "2026-09-18T14:00:00+00:00")
        self.assertEqual(args["limit"], 100)
        self.assertNotIn("force", args)
        with closing(get_connection(self.db_path)) as conn:
            row = conn.execute("SELECT * FROM marketdata_eod_sync_runs").fetchone()
            self.assertEqual(json.loads(row["summary_json"])["fetch_run_id"], result["fetch_run_id"])
            self.assertEqual(row["status"], "completed")
            self.assertIsNotNone(row["completed_at"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0], 0)

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

    def test_real_fetcher_excludes_recent_decisions_and_delays_gap_retries(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.executemany(
                """INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision, selected_option_symbol)
                   VALUES ('MSFT', ?, 'call', 'MSFT260117C00400000')""",
                [("2026-01-01T10:00:00+00:00",), ("2026-09-24T10:00:00+00:00",)],
            )
        response = Mock(status_code=203, headers={"X-Api-Ratelimit-Consumed": "1"})
        response.json.return_value = {"s": "no_data"}
        with patch("market_db_modules.backtest_marketdata.requests") as client:
            client.get.return_value = response
            result = run_due_eod_sync(EodSyncSettings(), db_path=self.db_path, now=self.now)
            self.assertEqual(result["source_decision_count"], 1)
            self.assertEqual(client.get.call_count, 1)
            with closing(get_connection(self.db_path)) as conn, conn:
                conn.execute("UPDATE historical_option_quote_fetch_runs SET started_at = ?", (self.now.isoformat(),))
            next_day = datetime.fromisoformat("2026-09-26T14:00:00+00:00")
            run_due_eod_sync(EodSyncSettings(), db_path=self.db_path, now=next_day)
            self.assertEqual(client.get.call_count, 1)
            # A week later the unresolved old window is eligible again.
            next_week = datetime.fromisoformat("2026-10-02T14:00:00+00:00")
            run_due_eod_sync(EodSyncSettings(), db_path=self.db_path, now=next_week)
            self.assertEqual(client.get.call_count, 2)

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

    def test_rate_limited_window_can_retry_next_day_without_weekly_gap_delay(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("""INSERT INTO manager_decision_history
                         (symbol, decision_run_at, manager_decision, selected_option_symbol)
                         VALUES ('MSFT', '2026-01-01T10:00:00+00:00', 'call', 'MSFT260117C00400000')""")
        response = Mock(status_code=429, headers={"Retry-After": "60"})
        response.raise_for_status.side_effect = RuntimeError("rate limited")
        with patch("market_db_modules.backtest_marketdata.requests") as client:
            client.get.return_value = response
            with patch("market_db_modules.marketdata_budget._now", return_value=self.now):
                first = run_due_eod_sync(EodSyncSettings(), db_path=self.db_path, now=self.now)
            self.assertEqual(first["status"], "paused")
            with closing(get_connection(self.db_path)) as conn, conn:
                conn.execute("UPDATE historical_option_quote_fetch_runs SET started_at = ?", (self.now.isoformat(),))
            next_day = datetime.fromisoformat("2026-09-26T14:00:00+00:00")
            with patch("market_db_modules.marketdata_budget._now", return_value=next_day):
                run_due_eod_sync(EodSyncSettings(), db_path=self.db_path, now=next_day)
            self.assertEqual(client.get.call_count, 2)

    def test_settings_validate_batch_size_and_timing(self):
        for name in ("MARKETDATA_AUTO_FETCH_BATCH_SIZE", "MARKETDATA_AUTO_FETCH_ENTRY_DELAY_MINUTES",
                     "MARKETDATA_AUTO_FETCH_MAX_HOLD_HOURS", "MARKETDATA_AUTO_FETCH_PUBLICATION_DELAY_HOURS",
                     "MARKETDATA_AUTO_FETCH_RETRY_DAYS"):
            with patch.dict("os.environ", {name: "0"}), self.assertRaises(ValueError):
                EodSyncSettings.from_env()
        with patch.dict("os.environ", {"MARKETDATA_AUTO_FETCH_BATCH_SIZE": "101"}), self.assertRaises(ValueError):
            EodSyncSettings.from_env()


if __name__ == "__main__":
    unittest.main()
