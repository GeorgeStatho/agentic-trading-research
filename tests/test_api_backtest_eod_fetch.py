from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from api_support.backtest import fetch_backtest_eod_quotes  # noqa: E402

try:
    from flask import Flask
    from api_routes.backtest import register_backtest_routes

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    FLASK_AVAILABLE = False


class BacktestEodFetchSupportTests(unittest.TestCase):
    def test_fetches_with_server_side_defaults_and_read_only_result(self) -> None:
        provider_result = {
            "fetch_run_id": 8,
            "provider": "marketdata_app",
            "pricing_source": "marketdata_eod",
            "status": "completed",
            "inserted_quote_count": 4,
        }
        with patch("api_support.backtest.fetch_marketdata_eod_option_quotes", return_value=provider_result) as fetcher:
            result = fetch_backtest_eod_quotes({"limit": 10})

        fetcher.assert_called_once_with(
            start_at=None,
            end_at=None,
            limit=10,
            entry_delay_minutes=1440,
            max_hold_hours=168,
            timeout_seconds=30.0,
            force=False,
        )
        self.assertTrue(result["read_only"])
        self.assertTrue(result["no_trade_executed_by_feature"])

    def test_rejects_invalid_client_settings(self) -> None:
        for payload in (
            {"limit": 0},
            {"limit": 101},
            {"entry_delay_minutes": 0},
            {"max_hold_hours": 0},
            {"timeout_seconds": 121},
            {"force": "yes"},
        ):
            with self.assertRaises(ValueError):
                fetch_backtest_eod_quotes(payload)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class BacktestEodFetchRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        register_backtest_routes(self.app)
        self.client = self.app.test_client()

    def test_route_rejects_non_object_and_invalid_requests(self) -> None:
        self.assertEqual(self.client.post("/api/backtests/marketdata-eod", json=[]).status_code, 400)
        self.assertEqual(self.client.post("/api/backtests/marketdata-eod", json={"limit": 0}).status_code, 400)

    def test_route_returns_fetch_summary(self) -> None:
        with patch("api_routes.backtest.fetch_backtest_eod_quotes", return_value={"fetch_run_id": 4, "read_only": True}):
            response = self.client.post("/api/backtests/marketdata-eod", json={"limit": 5})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"fetch_run_id": 4, "read_only": True})

