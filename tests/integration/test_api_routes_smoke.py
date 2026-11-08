"""Every GET route answers an empty, freshly set-up database without a server error."""
from __future__ import annotations

import importlib
import re
import unittest
from unittest import mock

from tests.db_fixtures import temporary_database


MISSING_ID = "999999"
# Routes that refuse an unauthenticated read by design.
AUTHENTICATED_ROUTES = {"/api/telemetry/summary"}
# Uptime monitors read 503 as "a loop is down"; no loop has written a status file in a test.
HEALTH_ROUTE_STATUS = {"/api/health/components": 503}
EMPTY_PORTFOLIO_HISTORY = {"timestamp": [], "equity": [], "profit_loss": [], "profit_loss_pct": [],
                           "base_value": 0, "timeframe": "1D"}


def _fake_alpaca(path: str, query: dict[str, str] | None = None):
    if path == "/v2/account":
        return {"equity": "1000", "last_equity": "1000", "cash": "1000", "buying_power": "1000",
                "portfolio_value": "1000", "status": "ACTIVE"}
    if path == "/v2/clock":
        return {"is_open": False, "next_open": "2026-11-09T14:30:00Z", "next_close": "2026-11-09T21:00:00Z",
                "timestamp": "2026-11-06T22:00:00Z"}
    if path.startswith("/v2/positions") or path.startswith("/v2/account/activities"):
        return []
    return {}


def _fake_alpaca_data(path: str, query: dict[str, str] | None = None):
    return {}


def _concrete_path(rule: str) -> str:
    return re.sub(r"<(?:[^:<>]+:)?[^<>]+>", MISSING_ID, rule)


class ApiRoutesSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.app = importlib.import_module("api").app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                raise unittest.SkipTest("Flask is not installed in this environment.") from exc
            raise

    def setUp(self) -> None:
        temporary_database(self)
        for target, fake in (
            ("api_support.dashboard.alpaca_get_json", _fake_alpaca),
            ("api_support.open_positions.alpaca_get_json", _fake_alpaca),
            ("api_support.open_positions.alpaca_data_get_json", _fake_alpaca_data),
            ("api_support.dashboard.fetch_portfolio_history", lambda *args, **kwargs: EMPTY_PORTFOLIO_HISTORY),
            ("api_routes.health.fetch_portfolio_history", lambda *args, **kwargs: EMPTY_PORTFOLIO_HISTORY),
        ):
            self.enterContext(mock.patch(target, side_effect=fake))
        self.client = self.app.test_client()

    def _get_rules(self):
        return sorted(rule.rule for rule in self.app.url_map.iter_rules()
                      if "GET" in rule.methods and rule.endpoint != "static")

    def test_every_get_route_answers_without_a_server_error(self):
        rules = self._get_rules()
        self.assertGreater(len(rules), 20)
        for rule in rules:
            with self.subTest(rule=rule):
                response = self.client.get(_concrete_path(rule))
                body = response.get_data(as_text=True)[:500]
                if rule in HEALTH_ROUTE_STATUS:
                    self.assertEqual(HEALTH_ROUTE_STATUS[rule], response.status_code, body)
                else:
                    self.assertLess(response.status_code, 500, body)

    def test_authenticated_routes_refuse_an_anonymous_read(self):
        for rule in AUTHENTICATED_ROUTES:
            with self.subTest(rule=rule):
                self.assertEqual(401, self.client.get(rule).status_code)


if __name__ == "__main__":
    unittest.main()
