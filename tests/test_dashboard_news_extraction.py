from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from datetime import date
from unittest import mock

from db_common import get_connection
from tests.db_fixtures import temporary_database

try:
    from flask import Flask
    from api_routes.news import register_news_routes

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    FLASK_AVAILABLE = False

from api_support.news import build_news_extraction_payload


class NewsExtractionPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)

    def test_window_counts_saved_and_failed_urls_per_source(self) -> None:
        with closing(get_connection()) as conn, conn:
            conn.executemany(
                "INSERT INTO news_articles (source, article_key, title, source_url, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    ("cnbc.com", "a", "A", "https://www.cnbc.com/a", "2026-10-28 09:00:00"),
                    ("cnbc.com", "b", "B", "https://www.cnbc.com/b", "2026-10-22 09:00:00"),
                    ("cnbc.com", "c", "C", "https://www.cnbc.com/c", "2026-10-21 23:59:59"),  # before the window
                ],
            )
            conn.execute(
                "INSERT INTO failed_urls (url, normalized_url, stage, last_error, is_permanent, updated_at) "
                "VALUES ('https://www.cnbc.com/d', 'cnbc.com/d', 'article_follow', 'HTTP 403', 1, '2026-10-27 10:00:00')"
            )
        payload = build_news_extraction_payload(7, today=date(2026, 10, 28))
        self.assertEqual((payload["start"], payload["end"]), ("2026-10-22", "2026-10-28"))
        (row,) = payload["sources"]
        self.assertEqual((row["source"], row["succeeded"], row["failed"]), ("cnbc.com", 2, 1))
        self.assertEqual(row["permanent_failures"], 1)
        self.assertEqual(row["top_error"], "HTTP 403")

    def test_connection_cannot_write(self) -> None:
        def write(conn, start, end):
            conn.execute("DELETE FROM news_articles")
            return []

        with mock.patch("api_support.news.extraction_success_by_source", side_effect=write):
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                build_news_extraction_payload(7)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class NewsExtractionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)
        app = Flask(__name__)
        register_news_routes(app)
        self.client = app.test_client()

    def test_empty_database_returns_report_shape(self) -> None:
        response = self.client.get("/api/news-extraction?days=30")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["days"], 30)
        self.assertEqual(body["sources"], [])

    def test_rejects_invalid_days(self) -> None:
        for value in ("0", "366", "abc"):
            response = self.client.get(f"/api/news-extraction?days={value}")
            self.assertEqual(response.status_code, 400, value)
            self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main()
