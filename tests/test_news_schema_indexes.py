from __future__ import annotations

from contextlib import closing
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from db_common import forget_setup, get_connection
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.article_store import add_news_article
from news_db_modules.bootstrap import initialize_database as initialize_news_database


class ArticleContentTableTests(unittest.TestCase):
    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db_path = Path(temp_dir.name) / "news-content.sqlite3"
        initialize_market_database(db_path=self.db_path)
        initialize_news_database(db_path=self.db_path)

    def content_rows(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            return [tuple(row) for row in conn.execute("SELECT article_id, body, raw_json FROM news_article_content")]

    def test_writer_stores_body_and_raw_json_outside_news_articles(self):
        article_id = add_news_article(
            "Test Wire", "article-1", "Title", "https://example.com/1",
            body="Body text", raw_json={"source": "Test Wire"}, db_path=self.db_path,
        )

        self.assertEqual(self.content_rows(), [(article_id, "Body text", '{"source": "Test Wire"}')])

    def test_setup_moves_inline_content_written_by_older_code(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("ALTER TABLE news_articles ADD COLUMN body TEXT")
            conn.execute("ALTER TABLE news_articles ADD COLUMN raw_json TEXT")
            conn.execute(
                """
                INSERT INTO news_articles (id, source, article_key, title, source_url, body, raw_json)
                VALUES (7, 'Test Wire', 'old-article', 'Old', 'https://example.com/old', 'Old body', '{}')
                """
            )
            conn.execute("UPDATE schema_bootstrap SET fingerprint = 'older-setup' WHERE name = 'news'")
            forget_setup(self.db_path)

        initialize_news_database(db_path=self.db_path)

        with closing(get_connection(self.db_path)) as conn, conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(news_articles)")}
        self.assertFalse({"body", "raw_json"} & columns)
        self.assertEqual(self.content_rows(), [(7, "Old body", "{}")])


class NewsSetupFingerprintTests(unittest.TestCase):
    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db_path = Path(temp_dir.name) / "news-setup.sqlite3"
        initialize_market_database(db_path=self.db_path)
        initialize_news_database(db_path=self.db_path)
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("DROP INDEX idx_news_articles_section")

    def news_article_indexes(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            return {row["name"] for row in conn.execute("PRAGMA index_list(news_articles)")}

    def test_database_set_up_by_other_files_is_migrated(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE schema_bootstrap SET fingerprint = 'older-setup' WHERE name = 'news'")
            forget_setup(self.db_path)

        initialize_news_database(db_path=self.db_path)

        self.assertIn("idx_news_articles_section", self.news_article_indexes())


if __name__ == "__main__":
    unittest.main()
