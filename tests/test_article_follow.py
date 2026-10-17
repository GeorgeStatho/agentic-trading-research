from __future__ import annotations

import json
import logging
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import partial
from unittest.mock import patch

from db_common import get_connection
from market_db_modules.entities import add_sector
from news_db_modules.links import link_sector_to_article, link_us_article
from pipelines._article_follow import NewsEntity, save_followed_article_links
from pipelines._orchestration import direct_article_save_request, run_article_save_requests
from pipelines._shared import ArticleExtractionResult
from tests.db_fixtures import temporary_database

LOGGER = logging.getLogger("test")
LOGGER.addHandler(logging.NullHandler())  # keep expected warnings out of test output


def _recent_url(name: str = "some-story") -> str:
    return f"https://www.cnbc.com/{datetime.now(timezone.utc):%Y/%m/%d}/{name}.html"


def _result(url: str, *, days_old: int = 0, **overrides) -> ArticleExtractionResult:
    published_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    fields = dict(url=url, title="T", text="body text " * 50, published_at=published_at, success=True)
    return ArticleExtractionResult(**{**fields, **overrides})


def _rows(sql: str, *params) -> list:
    with closing(get_connection()) as conn:
        return conn.execute(sql, params).fetchall()


class ArticleFollowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)
        self.sector_id = add_sector("technology", "Technology")
        self.sector = NewsEntity(
            kind="sector",
            id=self.sector_id,
            label="Technology",
            identity={"sector_id": self.sector_id, "sector_key": "technology", "sector_name": "Technology"},
            link=partial(link_sector_to_article, self.sector_id),
        )
        self.us = NewsEntity(kind="us", id=0, label="U.S.", identity={}, link=link_us_article)
        self.url = _recent_url()

    def _save(self, entity, text, fetched, url=None):
        url = url or self.url
        return save_followed_article_links(
            source_page_url="https://www.cnbc.com/finance/",
            candidate_links=[{"href": url, "text": text}],
            entity=entity,
            max_articles=5,
            fetched_articles=fetched,
            logger=LOGGER,
        )

    def _article_state(self):
        return _rows(
            "SELECT a.summary, c.raw_json FROM news_articles a JOIN news_article_content c ON c.article_id = a.id"
        )

    def test_new_article_is_inserted_and_linked(self):
        saved = self._save(self.sector, "desc", {self.url: _result(self.url)})

        self.assertEqual(saved, 1)
        (row,) = self._article_state()
        self.assertEqual(row["summary"], "desc")
        raw = json.loads(row["raw_json"])
        # raw_json is stored with sorted keys, so assert the key set rather than the order.
        self.assertEqual(
            set(raw),
            {
                "sector_id", "sector_key", "sector_name", "source_page_url", "link", "normalized_url",
                "normalized_title", "content_hash", "source_metadata", "scores", "extracted_article",
            },
        )
        self.assertEqual(raw["sector_key"], "technology")
        self.assertFalse(raw["extracted_article"]["reused_existing_article"])
        self.assertEqual(len(_rows("SELECT 1 FROM sector_news_articles WHERE sector_id = ?", self.sector_id)), 1)

    def test_reused_article_is_linked_without_touching_stored_article(self):
        self._save(self.sector, "desc", {self.url: _result(self.url)})
        before = self._article_state()[0]

        saved = self._save(self.us, "U.S. News", {})

        self.assertEqual(saved, 1)
        self.assertEqual(len(_rows("SELECT 1 FROM us_news_articles")), 1)
        (after,) = self._article_state()
        self.assertEqual((after["summary"], after["raw_json"]), (before["summary"], before["raw_json"]))

    def test_failed_fetch_is_recorded_and_not_saved(self):
        saved = self._save(self.sector, "desc", {self.url: _result(self.url, success=False, error="HTTP 500")})

        self.assertEqual(saved, 0)
        self.assertEqual(len(_rows("SELECT 1 FROM failed_urls")), 1)

    def test_stale_article_is_skipped(self):
        saved = self._save(self.sector, "desc", {self.url: _result(self.url, days_old=30)})

        self.assertEqual(saved, 0)
        self.assertEqual(_rows("SELECT 1 FROM news_articles"), [])

    def test_missing_result_saves_nothing_and_never_fetches(self):
        with patch("pipelines._article_follow.crawl_article_pages", side_effect=AssertionError("fetched")), patch(
            "pipelines._article_follow.fetch_articles_with_preferred_extractors", side_effect=AssertionError("fetched")
        ):
            saved = self._save(self.sector, "desc", {})

        self.assertEqual(saved, 0)
        self.assertEqual(_rows("SELECT 1 FROM news_articles"), [])

    def test_run_article_save_requests_keys_counts_by_entity(self):
        for by_source_type, key in ((False, self.sector_id), (True, (self.sector_id, "article"))):
            with self.subTest(by_source_type=by_source_type):
                urls = [_recent_url(f"{by_source_type}-one"), _recent_url(f"{by_source_type}-two")]
                fetched = {url: _result(url) for url in urls}
                requests = [direct_article_save_request(url, self.sector) for url in urls]

                with patch("pipelines._orchestration.fetch_articles_with_preferred_extractors", return_value=fetched):
                    counts = run_article_save_requests(
                        save_requests=requests, logger=LOGGER, by_source_type=by_source_type
                    )

                self.assertEqual(dict(counts), {key: 2})


if __name__ == "__main__":
    unittest.main()
