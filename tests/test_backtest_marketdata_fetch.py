from __future__ import annotations

import json
import sys
import tempfile
import unittest
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
from market_db import fetch_marketdata_eod_option_quotes, initialize_database, run_backtest  # noqa: E402


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
        self.assertEqual(requested[0][1], {"from": "2026-01-01", "to": "2026-01-09"})
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


if __name__ == "__main__":
    unittest.main()
