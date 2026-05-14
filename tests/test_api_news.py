from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

try:
    from api_support.news import build_analyzed_company_news_payload  # noqa: E402
    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_analyzed_company_news_payload = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class ApiNewsTests(unittest.TestCase):
    def test_analyzed_company_news_returns_full_company_article_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "news_test.sqlite3"
            recent_published_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            stale_published_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()

            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE sectors (
                        id INTEGER PRIMARY KEY,
                        sector_key TEXT,
                        name TEXT
                    );
                    CREATE TABLE industries (
                        id INTEGER PRIMARY KEY,
                        sector_id INTEGER,
                        industry_key TEXT,
                        name TEXT
                    );
                    CREATE TABLE companies (
                        id INTEGER PRIMARY KEY,
                        industry_id INTEGER,
                        symbol TEXT,
                        name TEXT
                    );
                    CREATE TABLE news_articles (
                        id INTEGER PRIMARY KEY,
                        title TEXT,
                        summary TEXT,
                        body TEXT,
                        source TEXT,
                        source_url TEXT,
                        published_at TEXT
                    );
                    CREATE TABLE company_opportunist_impacts (
                        article_id INTEGER,
                        company_id INTEGER,
                        confidence TEXT,
                        impact_direction TEXT,
                        impact_magnitude TEXT,
                        reason TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE company_opportunist_article_processing (
                        article_id INTEGER,
                        company_id INTEGER,
                        processed_at TEXT,
                        model TEXT
                    );
                    CREATE TABLE industry_opportunist_impacts (
                        article_id INTEGER,
                        industry_id INTEGER,
                        confidence TEXT,
                        impact_direction TEXT,
                        impact_magnitude TEXT,
                        reason TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE industry_opportunist_article_processing (
                        article_id INTEGER,
                        processed_at TEXT,
                        model TEXT
                    );
                    CREATE TABLE sector_opportunist_impacts (
                        article_id INTEGER,
                        sector_id INTEGER,
                        confidence TEXT,
                        impact_direction TEXT,
                        impact_magnitude TEXT,
                        reason TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE sector_opportunist_article_processing (
                        article_id INTEGER,
                        processed_at TEXT,
                        model TEXT
                    );
                    CREATE TABLE world_news_sector_impacts (
                        article_id INTEGER,
                        sector_id INTEGER,
                        confidence TEXT,
                        reason TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE world_news_article_processing (
                        article_id INTEGER,
                        processed_at TEXT,
                        model TEXT
                    );
                    CREATE TABLE us_news_sector_impacts (
                        article_id INTEGER,
                        sector_id INTEGER,
                        confidence TEXT,
                        reason TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE us_news_article_processing (
                        article_id INTEGER,
                        processed_at TEXT,
                        model TEXT
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO sectors (id, sector_key, name) VALUES (1, 'technology', 'Technology')"
                )
                conn.execute(
                    "INSERT INTO industries (id, sector_id, industry_key, name) VALUES (10, 1, 'software', 'Software')"
                )
                conn.execute(
                    "INSERT INTO companies (id, industry_id, symbol, name) VALUES (100, 10, 'AAPL', 'Apple Inc.')"
                )
                conn.executemany(
                    """
                    INSERT INTO news_articles (id, title, summary, body, source, source_url, published_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            1,
                            "Fresh article",
                            "Recent catalyst",
                            "Recent body",
                            "Test Wire",
                            "https://example.com/fresh",
                            recent_published_at,
                        ),
                        (
                            2,
                            "Stale article",
                            "Old catalyst",
                            "Old body",
                            "Test Wire",
                            "https://example.com/stale",
                            stale_published_at,
                        ),
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO company_opportunist_impacts
                    (article_id, company_id, confidence, impact_direction, impact_magnitude, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (1, 100, "high", "positive", "major", "Fresh reason", recent_published_at),
                        (2, 100, "high", "positive", "major", "Stale reason", stale_published_at),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            with patch(
                "api_support.news.get_current_pipeline_targets",
                return_value={
                    "selected_companies": [
                        {
                            "company_id": 100,
                            "symbol": "AAPL",
                            "name": "Apple Inc.",
                            "industry_key": "software",
                            "sector_key": "technology",
                        }
                    ]
                },
            ), patch("api_support.news.DB_PATH", db_path):
                payload = build_analyzed_company_news_payload(page=1, page_size=10)

        self.assertEqual(payload["company_count"], 1)
        self.assertEqual(payload["page_company_count"], 1)
        self.assertEqual(payload["article_count"], 2)
        self.assertEqual(payload["section_article_counts"]["company"], 2)

        company_entry = payload["companies"][0]
        company_articles = company_entry["company_news"]["articles"]
        self.assertEqual(len(company_articles), 2)
        self.assertEqual(company_articles[0]["title"], "Fresh article")
        self.assertEqual(company_articles[0]["assessments"][0]["reason"], "Fresh reason")
        self.assertEqual(company_articles[1]["title"], "Stale article")
        self.assertEqual(company_articles[1]["assessments"][0]["reason"], "Stale reason")


if __name__ == "__main__":
    unittest.main()
