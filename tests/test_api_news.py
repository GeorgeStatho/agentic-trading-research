from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from db_helpers import initialize_market_database, initialize_news_database
from market_db import add_company, add_industry, add_sector


try:
    from api_support.news import build_analyzed_company_news_payload  # noqa: E402
    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_analyzed_company_news_payload = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class DefaultCompanyListTests(unittest.TestCase):
    def test_companies_without_recent_company_news_are_not_listed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "news_default_companies.sqlite3"
            initialize_market_database(db_path=db_path)
            initialize_news_database(db_path=db_path)
            sector_id = add_sector("technology", "Technology", db_path=db_path)
            industry_id = add_industry(sector_id, "software", "Software", db_path=db_path)
            recent_company_id = add_company(industry_id, "AAPL", "Apple Inc.", db_path=db_path)
            expired_company_id = add_company(industry_id, "MSFT", "Microsoft", db_path=db_path)
            now = datetime.now(timezone.utc)
            conn = sqlite3.connect(db_path)
            try:
                conn.executemany(
                    """
                    INSERT INTO news_articles (id, source, article_key, title, source_url, published_at)
                    VALUES (?, 'Test Wire', ?, ?, ?, ?)
                    """,
                    [
                        (1, "recent", "Recent", "https://example.com/1", (now - timedelta(days=1)).isoformat()),
                        (2, "expired", "Expired", "https://example.com/2", (now - timedelta(days=60)).isoformat()),
                    ],
                )
                conn.executemany(
                    "INSERT INTO company_news_articles (company_id, article_id) VALUES (?, ?)",
                    [(recent_company_id, 1), (expired_company_id, 2)],
                )
                conn.commit()
            finally:
                conn.close()

            with patch("api_support.news.DB_PATH", db_path):
                payload = build_analyzed_company_news_payload(page=1, page_size=10)

        self.assertEqual([company["symbol"] for company in payload["companies"]], ["AAPL"])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class ApiNewsTests(unittest.TestCase):
    def test_analyzed_company_news_exposes_company_article_analysis_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "company_status_test.sqlite3"
            published_at = datetime.now(timezone.utc).isoformat()

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
                        source TEXT,
                        source_url TEXT,
                        published_at TEXT
                    );
                    CREATE TABLE news_article_content (
                        article_id INTEGER PRIMARY KEY,
                        body TEXT,
                        raw_json TEXT
                    );
                    CREATE TABLE company_news_articles (
                        company_id INTEGER,
                        article_id INTEGER
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
                    """
                )
                conn.execute(
                    "INSERT INTO sectors (id, sector_key, name) VALUES (1, 'technology', 'Technology')"
                )
                conn.execute(
                    "INSERT INTO industries (id, sector_id, industry_key, name) VALUES (10, 1, 'semiconductors', 'Semiconductors')"
                )
                conn.execute(
                    "INSERT INTO companies (id, industry_id, symbol, name) VALUES (100, 10, 'MU', 'Micron Technology, Inc.')"
                )
                conn.executemany(
                    """
                    INSERT INTO news_articles (id, title, summary, source, source_url, published_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (1, "Analyzed article", "Analyzed summary", "Test Wire", "https://example.com/1", published_at),
                        (2, "Processed no impact", "Processed summary", "Test Wire", "https://example.com/2", published_at),
                        (3, "Pending article", "Pending summary", "Test Wire", "https://example.com/3", published_at),
                    ],
                )
                conn.executemany(
                    "INSERT INTO news_article_content (article_id, body) VALUES (?, ?)",
                    [(1, "Analyzed body"), (2, "Processed body"), (3, "Pending body")],
                )
                conn.executemany(
                    "INSERT INTO company_news_articles (company_id, article_id) VALUES (?, ?)",
                    [(100, 1), (100, 2), (100, 3)],
                )
                conn.execute(
                    """
                    INSERT INTO company_opportunist_impacts
                    (article_id, company_id, confidence, impact_direction, impact_magnitude, reason, created_at)
                    VALUES (1, 100, 'high', 'positive', 'major', 'Clear upside catalyst', ?)
                    """,
                    (published_at,),
                )
                conn.execute(
                    """
                    INSERT INTO company_opportunist_article_processing
                    (article_id, company_id, processed_at, model)
                    VALUES (2, 100, ?, 'test-model')
                    """,
                    (published_at,),
                )
                conn.commit()
            finally:
                conn.close()

            with patch("api_support.news.DB_PATH", db_path):
                payload = build_analyzed_company_news_payload(page=1, page_size=10)

        company_articles = payload["companies"][0]["company_news"]["articles"]
        article_statuses = {article["title"]: article["analysis_status"] for article in company_articles}
        self.assertEqual(article_statuses["Analyzed article"], "analyzed")
        self.assertEqual(article_statuses["Processed no impact"], "processed_no_assessment")
        self.assertEqual(article_statuses["Pending article"], "pending")

    def test_analyzed_company_news_excludes_articles_outside_the_dashboard_age_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "news_test.sqlite3"
            recent_published_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            stale_published_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
            expired_published_at = (datetime.now(timezone.utc) - timedelta(days=11)).isoformat()

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
                        source TEXT,
                        source_url TEXT,
                        published_at TEXT
                    );
                    CREATE TABLE news_article_content (
                        article_id INTEGER PRIMARY KEY,
                        body TEXT,
                        raw_json TEXT
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
                    INSERT INTO news_articles (id, title, summary, source, source_url, published_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            1,
                            "Fresh article",
                            "Recent catalyst",
                            "Test Wire",
                            "https://example.com/fresh",
                            recent_published_at,
                        ),
                        (
                            2,
                            "Stale article",
                            "Old catalyst",
                            "Test Wire",
                            "https://example.com/stale",
                            stale_published_at,
                        ),
                        (
                            3,
                            "Expired article",
                            "Expired catalyst",
                            "Test Wire",
                            "https://example.com/expired",
                            expired_published_at,
                        ),
                    ],
                )
                conn.executemany(
                    "INSERT INTO news_article_content (article_id, body) VALUES (?, ?)",
                    [(1, "Recent body"), (2, "Old body"), (3, "Expired body")],
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
                        (3, 100, "high", "positive", "major", "Expired reason", expired_published_at),
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
        self.assertNotIn("Expired article", {article["title"] for article in company_articles})

    def test_analyzed_company_news_company_lookup_returns_specific_company_outside_pipeline_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "news_lookup_test.sqlite3"
            published_at = datetime.now(timezone.utc).isoformat()

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
                        source TEXT,
                        source_url TEXT,
                        published_at TEXT
                    );
                    CREATE TABLE news_article_content (
                        article_id INTEGER PRIMARY KEY,
                        body TEXT,
                        raw_json TEXT
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
                conn.executemany(
                    "INSERT INTO companies (id, industry_id, symbol, name) VALUES (?, 10, ?, ?)",
                    [
                        (100, "AAPL", "Apple Inc."),
                        (101, "MSFT", "Microsoft Corporation"),
                    ],
                )
                conn.execute(
                    """
                    INSERT INTO news_articles (id, title, summary, source, source_url, published_at)
                    VALUES (1, 'Microsoft article', 'MSFT catalyst', 'Test Wire', 'https://example.com/msft', ?)
                    """,
                    (published_at,),
                )
                conn.execute("INSERT INTO news_article_content (article_id, body) VALUES (1, 'MSFT body')")
                conn.execute(
                    """
                    INSERT INTO company_opportunist_impacts
                    (article_id, company_id, confidence, impact_direction, impact_magnitude, reason, created_at)
                    VALUES (1, 101, 'high', 'positive', 'major', 'MSFT reason', ?)
                    """,
                    (published_at,),
                )
                conn.commit()
            finally:
                conn.close()

            with patch(
                "api_support.news.get_current_pipeline_targets",
                return_value={"selected_companies": []},
            ), patch("api_support.news.DB_PATH", db_path):
                payload = build_analyzed_company_news_payload(
                    page=1,
                    page_size=10,
                    company_lookup="msft",
                )

        self.assertTrue(payload["lookup_active"])
        self.assertEqual(payload["lookup_query"], "msft")
        self.assertEqual(payload["lookup_match_count"], 1)
        self.assertEqual(payload["company_count"], 1)
        self.assertEqual(payload["companies"][0]["symbol"], "MSFT")
        self.assertEqual(payload["companies"][0]["company_news"]["articles"][0]["title"], "Microsoft article")


if __name__ == "__main__":
    unittest.main()
