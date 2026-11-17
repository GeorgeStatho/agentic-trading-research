from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.db_fixtures import temporary_database

from api_support.backtest import create_backtest_run  # noqa: E402

try:
    from flask import Flask
    from api_routes.backtest import register_backtest_routes

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    FLASK_AVAILABLE = False


class BacktestRunSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)

    def test_all_history_passes_no_limit_and_preserves_date_filters(self) -> None:
        with patch("api_support.backtest.run_backtest", return_value={"events": []}) as runner:
            create_backtest_run({"limit": None, "start_at": "2026-01-01", "end_at": "2026-09-24"})
        runner.assert_called_once_with(config=None, start_at="2026-01-01", end_at="2026-09-24", limit=None)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class BacktestEodFetchRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)
        self.app = Flask(__name__)
        register_backtest_routes(self.app)
        self.client = self.app.test_client()

    def test_route_rejects_non_object_and_invalid_requests(self) -> None:
        self.assertEqual(self.client.post("/api/backtests/marketdata-eod", json=[]).status_code, 400)
        self.assertEqual(self.client.post("/api/backtests/marketdata-eod", json={"limit": 0}).status_code, 400)



@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class BacktestMinuteBarFetchRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)
        self.app = Flask(__name__)
        register_backtest_routes(self.app)
        self.client = self.app.test_client()

    def test_route_rejects_invalid_requests(self) -> None:
        for payload in ([], {"limit": 0}, {"limit": 101}, {"max_hold_hours": "x"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/backtests/alpaca-minute-bars", json=payload).status_code, 400)

    def test_route_passes_the_window_to_the_fetch(self) -> None:
        with patch("api_support.backtest.fetch_alpaca_minute_bars", return_value={"fetched_decision_count": 2}) as fetch:
            response = self.client.post("/api/backtests/alpaca-minute-bars",
                                        json={"limit": 5, "max_hold_hours": 48, "start_at": "2026-11-01"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["read_only"])
        fetch.assert_called_once_with(start_at="2026-11-01", end_at=None, limit=5, max_hold_hours=48)
