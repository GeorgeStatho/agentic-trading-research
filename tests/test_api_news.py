from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from db_common import use_database
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from market_db_modules.entities import add_company, add_industry, add_sector


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

            with use_database(db_path):
                payload = build_analyzed_company_news_payload(page=1, page_size=10)

        self.assertEqual([company["symbol"] for company in payload["companies"]], ["AAPL"])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class ScopeCountsAndEvidenceTests(unittest.TestCase):
    """One company with two company articles, one sector and one industry article, and one US-macro article."""

    def payload(self, view: str):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "news_scopes.sqlite3"
            initialize_market_database(db_path=db_path)
            initialize_news_database(db_path=db_path)
            sector_id = add_sector("technology", "Technology", db_path=db_path)
            industry_id = add_industry(sector_id, "software", "Software", db_path=db_path)
            company_id = add_company(industry_id, "ORCL", "Oracle", db_path=db_path)
            recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
            conn = sqlite3.connect(db_path)
            try:
                conn.executemany(
                    "INSERT INTO news_articles (id, source, article_key, title, source_url, published_at, evidence_score) VALUES (?, 'Test Wire', ?, ?, '', ?, ?)",
                    [(article_id, f"a{article_id}", f"Article {article_id}", recent, score)
                     for article_id, score in ((1, 0.81), (2, None), (3, 0.5), (4, 0.4), (5, 0.3))],
                )
                conn.executemany("INSERT INTO company_news_articles (company_id, article_id) VALUES (?, ?)", [(company_id, 1), (company_id, 2)])
                conn.execute("INSERT INTO sector_opportunist_impacts (article_id, sector_id, confidence, reason) VALUES (3, ?, 'medium', 'sector')", (sector_id,))
                conn.execute("INSERT INTO industry_opportunist_impacts (article_id, industry_id, confidence, reason) VALUES (4, ?, 'low', 'industry')", (industry_id,))
                conn.execute("INSERT INTO us_news_sector_impacts (article_id, sector_id, confidence, reason) VALUES (5, ?, 'high', 'macro')", (sector_id,))
                conn.commit()
            finally:
                conn.close()

            with use_database(db_path):
                return build_analyzed_company_news_payload(page=1, page_size=10, view=view)

    def test_counts_articles_in_every_scope_whichever_is_viewed(self):
        expected = {"company": 2, "sector": 1, "industry": 1, "macro": 1}
        for view in expected:
            with self.subTest(view=view):
                self.assertEqual(self.payload(view)["section_article_counts"], expected)

    def test_sends_only_the_viewed_scope_articles_and_counts_that_scope(self):
        payload = self.payload("sector")
        company = payload["companies"][0]

        self.assertEqual([article["article_id"] for article in company["sector_news"]["articles"]], [3])
        self.assertEqual(company["company_news"]["articles"], [])
        self.assertEqual(payload["article_count"], 1)

    def test_each_article_carries_its_evidence_score(self):
        articles = {article["article_id"]: article for article in self.payload("company")["companies"][0]["company_news"]["articles"]}

        self.assertEqual(articles[1]["evidence_score"], 0.81)
        self.assertIsNone(articles[2]["evidence_score"])


if __name__ == "__main__":
    unittest.main()
