from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import Mock, patch
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"

from db_common import get_connection  # noqa: E402
from market_db import fetch_marketdata_eod_option_quotes, initialize_database, run_backtest  # noqa: E402
from market_db_modules.marketdata_budget import MarketDataBudget  # noqa: E402


class _FakeResponse:
    status_code = 203
    headers = {"X-Api-Ratelimit-Consumed": "1", "X-Api-Ratelimit-Remaining": "99"}

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class MarketDataEodQuoteFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": "100"})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "marketdata_fetch.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision, selected_option_symbol)
                VALUES ('MSFT', '2026-01-01T10:00:00+00:00', 'call', 'MSFT260117C00400000')
                """
            )
            conn.execute(
                """
                INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision, selected_option_symbol)
                VALUES ('AAPL', '2026-01-01T10:00:00+00:00', 'put', 'AAPL260117P00200000')
                """
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _quotes(symbol, days):
        return _FakeResponse({
            "s": "ok", "optionSymbol": [symbol] * len(days),
            "bid": [1.0] * len(days), "ask": [1.1] * len(days),
            "updated": [int(datetime.fromisoformat(day).timestamp()) for day in days],
        })

    def test_batches_advance_past_failures_instead_of_repeating_latest_decision(self) -> None:
        requested = []
        def fake_get(url, **kwargs):
            requested.append(url)
            return _FakeResponse({"s": "no_data", "updated": []})
        first = fetch_marketdata_eod_option_quotes(limit=1, db_path=self.db_path, token="token", request_get=fake_get)
        second = fetch_marketdata_eod_option_quotes(limit=1, db_path=self.db_path, token="token", request_get=fake_get)
        self.assertIn("AAPL", requested[0])
        self.assertIn("MSFT", requested[1])
        self.assertEqual(first["remaining_decision_count"], 1)
        self.assertEqual(second["source_decision_count"], 1)

    def test_completed_cached_windows_do_not_consume_the_next_batch(self) -> None:
        requested = []
        def fake_get(url, **kwargs):
            requested.append(url)
            symbol = url.rstrip('/').split('/')[-1]
            return self._quotes(symbol, ["2026-01-01T21:00:00+00:00", "2026-01-02T21:00:00+00:00", "2026-01-09T21:00:00+00:00"])
        first = fetch_marketdata_eod_option_quotes(limit=1, db_path=self.db_path, token="token", request_get=fake_get)
        second = fetch_marketdata_eod_option_quotes(limit=1, db_path=self.db_path, token="token", request_get=fake_get)
        third = fetch_marketdata_eod_option_quotes(limit=1, db_path=self.db_path, token="token", request_get=fake_get)
        self.assertIn("AAPL", requested[0])
        self.assertIn("MSFT", requested[1])
        self.assertEqual(len(requested), 2)
        self.assertEqual(second["already_cached_count"], 1)
        self.assertEqual(third["already_cached_count"], 2)
        self.assertEqual(third["source_decision_count"], 0)
        self.assertEqual(third["budget"]["used"], 2)
        self.assertEqual(first["returned_quote_end_at"], "2026-01-09T21:00:00+00:00")
        self.assertEqual(first["latest_contract_decision_at"], "2026-01-01T10:00:00+00:00")
        fetch_marketdata_eod_option_quotes(limit=1, force=True, db_path=self.db_path, token="token", request_get=fake_get)
        self.assertEqual(len(requested), 3)

    def test_imports_marketdata_parallel_quote_arrays_and_labels_backtest_events(self) -> None:
        requested: list[tuple[str, dict]] = []

        def fake_get(url, *, headers, params, timeout):
            self.assertEqual(headers["Authorization"], "Bearer token")
            self.assertEqual(timeout, 30.0)
            requested.append((url, params))
            if "AAPL260117P00200000" in url:
                return _FakeResponse({"s": "no_data", "updated": []})
            return _FakeResponse({
                "s": "ok",
                "optionSymbol": ["MSFT260117C00400000", "MSFT260117C00400000"],
                "bid": [1.0, 1.5],
                "ask": [1.1, 1.6],
                "last": [1.05, 1.55],
                "volume": [20, 35],
                "openInterest": [100, 101],
                "iv": [0.3, 0.31],
                "delta": [0.5, 0.55],
                "gamma": [0.02, 0.02],
                "theta": [-0.01, -0.01],
                "vega": [0.1, 0.11],
                "updated": [1767283260, 1767369900],
            })

        fetch_result = fetch_marketdata_eod_option_quotes(
            db_path=self.db_path, token="token", request_get=fake_get,
        )
        backtest_result = run_backtest(
            config={"entry_slippage_bps": 0, "exit_slippage_bps": 0, "commission_per_contract": 0},
            db_path=self.db_path,
        )

        self.assertEqual(fetch_result["fetched_count"], 1)
        self.assertEqual(fetch_result["no_data_count"], 1)
        self.assertEqual(fetch_result["inserted_quote_count"], 2)
        self.assertEqual(len(requested), 2)
        self.assertEqual(requested[0][1], {"from": "2026-01-01", "to": "2026-01-10"})
        event = next(item for item in backtest_result["events"] if item["symbol"] == "MSFT")
        self.assertEqual(event["status"], "simulated_closed")
        self.assertEqual(event["pricing_source"], "marketdata_eod")
        self.assertEqual(event["quote_provider"], "marketdata_app")
        with closing(get_connection(self.db_path)) as conn, conn:
            stored = conn.execute("SELECT raw_json FROM historical_option_quotes WHERE option_symbol = 'MSFT260117C00400000' LIMIT 1").fetchone()
            coverage = conn.execute("SELECT status FROM historical_option_quote_coverage ORDER BY id").fetchall()
        self.assertEqual(json.loads(stored["raw_json"])["pricing_source"], "marketdata_eod")
        self.assertEqual([row["status"] for row in coverage], ["fetched", "no_data"])

    def test_daily_budget_persists_across_batches_and_force(self) -> None:
        fake_get = Mock(return_value=_FakeResponse({"s": "no_data", "updated": []}))
        with patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": "1"}):
            first = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
            second = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get, force=True)
        self.assertEqual(fake_get.call_count, 1)
        self.assertEqual(first["status"], "paused")
        self.assertEqual(first["deferred_decision_count"], 1)
        self.assertEqual(second["deferred_decision_count"], 2)
        self.assertEqual(second["budget"]["used"], 1)
        self.assertEqual(second["stop_reason"], "daily_credit_budget_exhausted")
        with closing(get_connection(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM historical_option_quote_coverage").fetchone()[0], 1)

    def test_429_stops_batch_and_blocks_new_instances_until_retry_after(self) -> None:
        now = datetime.fromisoformat("2026-09-25T15:00:00+00:00")
        response = _FakeResponse({})
        response.status_code = 429
        response.headers = {"Retry-After": "60", "X-Api-Ratelimit-Remaining": "20"}
        response.raise_for_status = Mock(side_effect=RuntimeError("rate limited"))
        fake_get = Mock(return_value=response)
        with patch("market_db_modules.marketdata_budget._now", return_value=now):
            first = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
            second = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
        self.assertEqual(fake_get.call_count, 1)
        self.assertEqual(first["rate_limited_count"], 1)
        self.assertEqual(first["remaining_decision_count"], 2)
        self.assertEqual(second["stop_reason"], "provider_rate_limited")
        self.assertEqual(second["resume_at"], "2026-09-25T15:01:00+00:00")
        with patch("market_db_modules.marketdata_budget._now", return_value=datetime.fromisoformat(second["resume_at"])):
            self.assertIsNotNone(MarketDataBudget(self.db_path).reserve()[0])

    def test_success_exhausting_provider_credits_keeps_quotes_and_stops(self) -> None:
        response = self._quotes("MSFT260117C00400000", ["2026-01-01T21:00:00+00:00", "2026-01-09T21:00:00+00:00"])
        reset = datetime.fromisoformat("2026-09-26T13:30:00+00:00")
        response.headers = {"x-api-ratelimit-consumed": "3", "x-api-ratelimit-remaining": "0", "x-api-ratelimit-reset": str(int(reset.timestamp()))}
        fake_get = Mock(return_value=response)
        with patch("market_db_modules.marketdata_budget._now", return_value=datetime.fromisoformat("2026-09-25T15:00:00+00:00")):
            result = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
            fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get, force=True)
        self.assertEqual(fake_get.call_count, 1)
        self.assertEqual(result["inserted_quote_count"], 2)
        self.assertEqual(result["budget"]["used"], 3)
        self.assertEqual(result["stop_reason"], "provider_credits_exhausted")
        self.assertEqual(result["resume_at"], reset.isoformat())
        self.assertEqual(result["deferred_decision_count"], 1)

    def test_atomic_reservations_prevent_concurrent_budget_reuse(self) -> None:
        with patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": "3"}):
            with ThreadPoolExecutor(max_workers=6) as executor:
                reservations = list(executor.map(lambda _: MarketDataBudget(self.db_path).reserve()[0], range(12)))
        self.assertEqual(sum(value is not None for value in reservations), 3)

    def test_zero_budget_and_invalid_configuration_do_not_send_requests(self) -> None:
        fake_get = Mock()
        with patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": "0"}):
            result = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
        self.assertEqual(result["status"], "paused")
        for setting in ("-1", "unlimited", "1.5"):
            with patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": setting}), self.assertRaises(ValueError):
                fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
        fake_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
