import unittest
from contextlib import closing

from db_common import get_connection
from market_db_modules.entities import add_sector
from news_db_modules.article_store import add_news_article
from scripts import clear_placeholder_summaries

from tests.db_fixtures import temporary_database


class ClearPlaceholderSummariesTests(unittest.TestCase):
    def setUp(self):
        temporary_database(self)
        add_sector("technology", "Technology")
        for key, summary in (("a", "Technology"), ("b", "U.S. News"), ("c", "real")):
            add_news_article("test", key, f"title {key}", f"https://example.com/{key}", summary=summary)

    @staticmethod
    def summaries():
        with closing(get_connection()) as conn:
            return [row[0] for row in conn.execute("SELECT summary FROM news_articles ORDER BY article_key")]

    def test_dry_run_counts_and_changes_nothing(self):
        counts = clear_placeholder_summaries.clear_placeholder_summaries()
        self.assertEqual(counts, {"Technology": 1, "U.S. News": 1})
        self.assertEqual(self.summaries(), ["Technology", "U.S. News", "real"])

    def test_apply_clears_placeholders_only(self):
        clear_placeholder_summaries.clear_placeholder_summaries(apply=True)
        self.assertEqual(self.summaries(), [None, None, "real"])


if __name__ == "__main__":
    unittest.main()
