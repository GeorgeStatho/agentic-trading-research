from __future__ import annotations

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
from market_db import fetch_alpaca_historical_option_quotes, initialize_database  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = RuntimeError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self) -> dict:
        return self.payload


class AlpacaHistoricalQuoteFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "alpaca_fetch.db"
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

    def test_fetches_paginated_quotes_and_records_no_data_coverage_gaps(self) -> None:
        requested_pages: list[dict] = []

        def fake_get(_url, *, headers, params, timeout):
            self.assertEqual(headers["APCA-API-KEY-ID"], "key")
            self.assertEqual(headers["APCA-API-SECRET-KEY"], "secret")
            self.assertEqual(timeout, 30.0)
            requested_pages.append(params)
            if params["symbols"] == "AAPL260117P00200000":
                return _FakeResponse({"quotes": {"AAPL260117P00200000": []}})
            if not params.get("page_token"):
                return _FakeResponse({
                    "quotes": {"MSFT260117C00400000": [{"t": "2026-01-01T10:01:00.123456789Z", "bp": 1.0, "ap": 1.1}]},
                    "next_page_token": "next-page",
                })
            return _FakeResponse({
                "quotes": {"MSFT260117C00400000": [{"t": "2026-01-01T10:05:00Z", "bp": 1.5, "ap": 1.6}]},
                "next_page_token": None,
            })

        result = fetch_alpaca_historical_option_quotes(
            db_path=self.db_path, api_key="key", api_secret="secret", request_get=fake_get,
        )

        self.assertEqual(result["status"], "completed_with_gaps")
        self.assertEqual(result["fetched_count"], 1)
        self.assertEqual(result["no_data_count"], 1)
        self.assertEqual(result["inserted_quote_count"], 2)
        self.assertEqual(len(requested_pages), 3)
        with get_connection(self.db_path) as conn:
            quotes = conn.execute("SELECT option_symbol, bid_price, ask_price FROM historical_option_quotes ORDER BY quote_at").fetchall()
            coverage = conn.execute("SELECT option_symbol, status, quote_count, page_count FROM historical_option_quote_coverage ORDER BY id").fetchall()
            run = conn.execute("SELECT status FROM historical_option_quote_fetch_runs WHERE id = ?", (result["fetch_run_id"],)).fetchone()
        self.assertEqual(len(quotes), 2)
        self.assertEqual(quotes[0]["bid_price"], 1.0)
        self.assertEqual([dict(row) for row in coverage], [
            {"option_symbol": "MSFT260117C00400000", "status": "fetched", "quote_count": 2, "page_count": 2},
            {"option_symbol": "AAPL260117P00200000", "status": "no_data", "quote_count": 0, "page_count": 1},
        ])
        self.assertEqual(run["status"], "completed_with_gaps")

    def test_missing_credentials_records_unavailable_coverage_without_requests(self) -> None:
        result = fetch_alpaca_historical_option_quotes(db_path=self.db_path, api_key="", api_secret="", request_get=None)

        self.assertEqual(result["unavailable_count"], 2)
        self.assertEqual(result["inserted_quote_count"], 0)
        with get_connection(self.db_path) as conn:
            statuses = [row["status"] for row in conn.execute("SELECT status FROM historical_option_quote_coverage ORDER BY id").fetchall()]
        self.assertEqual(statuses, ["unavailable", "unavailable"])

    def test_missing_historical_quote_endpoint_records_one_provider_gap_without_retries(self) -> None:
        requests_made = 0

        def missing_endpoint(*_args, **_kwargs):
            nonlocal requests_made
            requests_made += 1
            return _FakeResponse({}, status_code=404)

        result = fetch_alpaca_historical_option_quotes(
            db_path=self.db_path, api_key="key", api_secret="secret", request_get=missing_endpoint,
        )

        self.assertEqual(requests_made, 1)
        self.assertFalse(result["historical_bid_ask_supported"])
        self.assertEqual(result["unavailable_count"], 2)
        self.assertEqual(
            {gap["reason"] for gap in result["coverage_gaps"]},
            {"alpaca_historical_option_bid_ask_endpoint_unavailable"},
        )


if __name__ == "__main__":
    unittest.main()
