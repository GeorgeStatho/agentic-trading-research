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


if __name__ == "__main__":
    unittest.main()
