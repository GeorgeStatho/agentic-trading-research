from __future__ import annotations

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import Mock, patch
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from db_common import get_connection  # noqa: E402
from market_db import fetch_marketdata_eod_option_quotes, initialize_database, run_backtest, record_historical_option_quote  # noqa: E402
from market_db_modules.backtest_marketdata import _request_marketdata_quotes  # noqa: E402
from market_db_modules.marketdata_budget import MarketDataBudget, _window  # noqa: E402


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
        with get_connection(self.db_path) as conn:
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

    def test_two_cached_quotes_do_not_hide_an_incomplete_holding_window(self) -> None:
        for timestamp in ("2026-01-01T21:00:00+00:00", "2026-01-02T21:00:00+00:00"):
            record_historical_option_quote({"option_symbol": "AAPL260117P00200000", "quote_at": timestamp, "bid": 1.0, "ask": 1.1}, provider="marketdata_app", db_path=self.db_path)
        requested = []
        def fake_get(url, **kwargs):
            requested.append(url)
            symbol = url.rstrip('/').split('/')[-1]
            return self._quotes(symbol, ["2026-01-01T21:00:00+00:00", "2026-01-02T21:00:00+00:00"])
        for _ in range(3):
            fetch_marketdata_eod_option_quotes(limit=1, db_path=self.db_path, token="token", request_get=fake_get)
        self.assertEqual(len(requested), 3)
        self.assertIn("AAPL", requested[0])
        self.assertIn("AAPL", requested[2])

    def test_request_includes_final_day_and_uses_market_dates(self) -> None:
        requested = []
        def fake_get(url, **kwargs):
            requested.append(kwargs["params"])
            return _FakeResponse({"s": "no_data", "updated": []})
        _request_marketdata_quotes(option_symbol="MSFT260117C00400000", start_at="2026-01-02T01:00:00+00:00", end_at="2026-01-10T01:00:00+00:00", token="token", timeout_seconds=30, request_get=fake_get)
        self.assertEqual(requested, [{"from": "2026-01-01", "to": "2026-01-10"}])

    def test_fetch_adds_september_quotes_and_prices_recent_decision_in_all_history(self) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision, selected_option_symbol) VALUES ('NVDA', '2026-09-10T14:00:00+00:00', 'call', 'NVDA261016C00200000')"
            )
        requested = []
        def fake_get(url, **kwargs):
            requested.append(kwargs["params"])
            return self._quotes("NVDA261016C00200000", ["2026-09-10T20:00:00+00:00", "2026-09-11T20:00:00+00:00", "2026-09-18T20:00:00+00:00"])
        result = fetch_marketdata_eod_option_quotes(limit=1, db_path=self.db_path, token="token", request_get=fake_get)
        self.assertEqual(requested, [{"from": "2026-09-10", "to": "2026-09-19"}])
        self.assertEqual(result["returned_quote_end_at"], "2026-09-18T20:00:00+00:00")
        replay = run_backtest(limit=None, db_path=self.db_path)
        recent = next(event for event in replay["events"] if event["symbol"] == "NVDA")
        self.assertEqual(recent["status"], "simulated_closed")
        self.assertEqual(recent["pricing_source"], "marketdata_eod")

    def test_limited_fetch_reaches_recent_contracts_and_respects_date_bounds(self) -> None:
        with get_connection(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision, selected_option_symbol) VALUES (?, ?, ?, ?)",
                [
                    ("NVDA", "2026-09-20T10:00:00+00:00", "call", "NVDA261016C00200000"),
                    ("AMD", "2026-09-20T10:00:00+00:00", "call", "AMD261016C00200000"),
                    ("MU", "2026-09-21T10:00:00+00:00", "call", None),
                    ("IBM", "2026-09-21T10:00:00+00:00", "neither", "IBM261016C00200000"),
                ],
            )
        requested = []

        def fake_get(url, **kwargs):
            requested.append(url)
            return _FakeResponse({"s": "no_data", "updated": []})

        result = fetch_marketdata_eod_option_quotes(
            limit=1, db_path=self.db_path, token="token", request_get=fake_get,
        )
        self.assertEqual(result["source_decision_count"], 1)
        self.assertEqual(len(requested), 1)
        self.assertIn("AMD261016C00200000", requested[0])

        requested.clear()
        fetch_marketdata_eod_option_quotes(
            start_at="2026-01-01T10:00:00+00:00", end_at="2026-01-01T10:00:00+00:00",
            limit=1, db_path=self.db_path, token="token", request_get=fake_get,
        )
        self.assertEqual(len(requested), 1)
        self.assertIn("AAPL260117P00200000", requested[0])

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
        with get_connection(self.db_path) as conn:
            stored = conn.execute("SELECT raw_json FROM historical_option_quotes WHERE option_symbol = 'MSFT260117C00400000' LIMIT 1").fetchone()
            coverage = conn.execute("SELECT status FROM historical_option_quote_coverage ORDER BY id").fetchall()
        self.assertEqual(json.loads(stored["raw_json"])["pricing_source"], "marketdata_eod")
        self.assertEqual([row["status"] for row in coverage], ["fetched", "no_data"])

    def test_missing_token_records_coverage_gaps_without_requests(self) -> None:
        result = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="", request_get=None)
        self.assertEqual(result["unavailable_count"], 2)
        self.assertEqual(result["inserted_quote_count"], 0)
        self.assertEqual(result["budget"]["used"], 0)

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

    def test_network_errors_and_free_responses_still_bound_retries(self) -> None:
        response = _FakeResponse({"s": "no_data"})
        response.headers = {"X-Api-Ratelimit-Consumed": "0"}
        fake_get = Mock(side_effect=[TimeoutError("timeout"), response])
        with patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": "2"}):
            result = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
            fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
        self.assertEqual(fake_get.call_count, 2)
        self.assertEqual(result["budget"]["used"], 2)

    def test_atomic_reservations_prevent_concurrent_budget_reuse(self) -> None:
        with patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": "3"}):
            with ThreadPoolExecutor(max_workers=6) as executor:
                reservations = list(executor.map(lambda _: MarketDataBudget(self.db_path).reserve()[0], range(12)))
        self.assertEqual(sum(value is not None for value in reservations), 3)

    def test_rate_limit_header_fallbacks_and_cooldown_across_daily_reset(self) -> None:
        now = datetime.fromisoformat("2026-09-25T13:29:00+00:00")
        cases = [
            ({"Retry-After": "Fri, 25 Sep 2026 13:31:00 GMT"}, "2026-09-25T13:31:00+00:00"),
            ({"Retry-After": "invalid", "X-Api-Ratelimit-Reset": "NaN"}, "2026-09-25T13:30:00+00:00"),
            ({}, "2026-09-25T13:30:00+00:00"),
        ]
        for headers, expected in cases:
            with self.subTest(headers=headers):
                with closing(get_connection(self.db_path)) as conn, conn:
                    conn.execute("DELETE FROM marketdata_request_budget")
                budget = MarketDataBudget(self.db_path)
                response = _FakeResponse({})
                response.status_code = 429
                response.headers = headers
                with patch("market_db_modules.marketdata_budget._now", return_value=now):
                    reservation, _ = budget.reserve()
                    summary = budget.observe(reservation, response)
                self.assertEqual(summary["resume_at"], expected)
                if expected.endswith("13:31:00+00:00"):
                    with patch("market_db_modules.marketdata_budget._now", return_value=datetime.fromisoformat("2026-09-25T13:30:00+00:00")):
                        self.assertIsNone(budget.reserve()[0])

    def test_reservations_do_not_hold_database_lock_during_request(self) -> None:
        def fake_get(url, **kwargs):
            # A separate writer must be able to commit while the request runs.
            with closing(get_connection(self.db_path)) as conn, conn:
                conn.execute("UPDATE manager_decision_history SET symbol = symbol")
            return _FakeResponse({"s": "no_data"})
        result = fetch_marketdata_eod_option_quotes(db_path=self.db_path, token="token", request_get=fake_get)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["no_data_count"], 2)

    def test_budget_reset_is_market_open_and_handles_dst(self) -> None:
        self.assertEqual(_window(datetime.fromisoformat("2026-03-08T12:00:00+00:00")),
                         ("2026-03-07T14:30:00+00:00", "2026-03-08T13:30:00+00:00"))
        with patch.dict("os.environ", {"MARKETDATA_DAILY_CREDIT_BUDGET": "1"}):
            budget = MarketDataBudget(self.db_path)
            with patch("market_db_modules.marketdata_budget._now", return_value=datetime.fromisoformat("2026-09-25T15:00:00+00:00")):
                self.assertIsNotNone(budget.reserve()[0])
            with patch("market_db_modules.marketdata_budget._now", return_value=datetime.fromisoformat("2026-09-26T13:29:59+00:00")):
                self.assertIsNone(budget.reserve()[0])
            with patch("market_db_modules.marketdata_budget._now", return_value=datetime.fromisoformat("2026-09-26T13:30:00+00:00")):
                self.assertIsNotNone(budget.reserve()[0])

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
