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
class NewsLagRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)
        app = Flask(__name__)
        register_dashboard_routes(app)
        self.client = app.test_client()

    def test_empty_database_returns_report_shape(self) -> None:
        response = self.client.get("/api/news-lag?days=90")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["days"], 90)
        self.assertEqual(body["headline"]["entries"], 0)
        self.assertIsNone(body["headline"]["median_lag_hours"])
        self.assertEqual(set(body["buckets"]), {"<1h", "1-6h", "overnight", ">1d", "unknown"})
        self.assertEqual(body["trades"], [])

    def test_rejects_invalid_days(self) -> None:
        for value in ("-1", "abc"):
            response = self.client.get(f"/api/news-lag?days={value}")
            self.assertEqual(response.status_code, 400, value)
            self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main()
