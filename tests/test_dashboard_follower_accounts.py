from __future__ import annotations

import unittest

from tests.db_fixtures import temporary_database

try:
    from flask import Flask
    from api_routes.dashboard import register_dashboard_routes

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class FollowerAccountsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)
        app = Flask(__name__)
        register_dashboard_routes(app)
        self.client = app.test_client()

    def test_empty_database_returns_report_shape(self) -> None:
        response = self.client.get("/api/follower-accounts?days=90")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual((body["days"], body["accounts"], body["trades"]), (90, {}, []))
        self.assertIn("as_of", body)

    def test_defaults_to_all_history(self) -> None:
        response = self.client.get("/api/follower-accounts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["days"], 0)

    def test_rejects_invalid_days(self) -> None:
        for value in ("-1", "abc"):
            response = self.client.get(f"/api/follower-accounts?days={value}")
            self.assertEqual(response.status_code, 400, value)
            self.assertIn("error", response.get_json())

    def test_entry_kind_selects_the_drift_entries_and_rejects_unknown_kinds(self) -> None:
        from unittest.mock import patch
        with patch("market_db_modules.account_positions.follower_report",
                   return_value={"as_of": None, "days": 0, "accounts": {}, "trades": []}) as report:
            self.assertEqual(self.client.get("/api/follower-accounts?entry_kind=shares_drift").status_code, 200)
        report.assert_called_once_with(days=0, entry_kind="shares_drift")
        response = self.client.get("/api/follower-accounts?entry_kind=llm")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
