from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from db_common import get_connection
from market_db import initialize_database as initialize_market_database
from news_db import initialize_database as initialize_news_database


class NewsSchemaIndexTests(unittest.TestCase):
    def test_news_dashboard_join_indexes_are_created(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "news-indexes.sqlite3"
            initialize_market_database(db_path=db_path)
            initialize_news_database(db_path=db_path)
            with get_connection(db_path) as conn:
                index_names = {
                    row["name"]
                    for table_name in (
                        "company_opportunist_impacts",
                        "industry_opportunist_impacts",
                        "sector_opportunist_impacts",
                        "world_news_sector_impacts",
                        "us_news_sector_impacts",
                    )
                    for row in conn.execute(f"PRAGMA index_list({table_name})").fetchall()
                }

        self.assertTrue(
            {
                "idx_company_opportunist_impacts_company_article",
                "idx_industry_opportunist_impacts_industry_article",
                "idx_sector_opportunist_impacts_sector_article",
                "idx_world_news_sector_impacts_sector_article",
                "idx_us_news_sector_impacts_sector_article",
            }.issubset(index_names)
        )


if __name__ == "__main__":
    unittest.main()
