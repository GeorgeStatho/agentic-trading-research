from __future__ import annotations

import json
import logging
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from functools import partial
from unittest.mock import patch

from db_common import get_connection
from engine.page_fetch import FixtureFetcher, use_fetcher
from market_db_modules.entities import add_sector
from news_db_modules.links import link_sector_to_article, link_us_article
from pipelines._article_follow import NewsEntity, save_followed_article_links
from pipelines._regionNewsPipeline import make_region_pipeline
from pipelines._orchestration import (
    direct_article_save_request,
    run_article_save_requests,
    run_mixed_job_orchestration,
)
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
        with use_fetcher(FixtureFetcher()) as fixture:
            saved = self._save(self.sector, "desc", {})

        self.assertEqual(saved, 0)
        self.assertEqual(fixture.requested, [])
        self.assertEqual(_rows("SELECT 1 FROM news_articles"), [])

    def test_run_article_save_requests_keys_counts_by_entity(self):
        for by_source_type, key in ((False, self.sector_id), (True, (self.sector_id, "article"))):
            with self.subTest(by_source_type=by_source_type):
                urls = [_recent_url(f"{by_source_type}-one"), _recent_url(f"{by_source_type}-two")]
                fetched = {url: _result(url) for url in urls}
                requests = [direct_article_save_request(url, self.sector) for url in urls]

                with use_fetcher(FixtureFetcher(articles=fetched)):
                    counts = run_article_save_requests(
                        save_requests=requests, logger=LOGGER, by_source_type=by_source_type
                    )

                self.assertEqual(dict(counts), {key: 2})

    def test_region_rss_description_becomes_summary_and_links_us_article(self):
        description = "Stocks rally on earnings"
        rss_xml = (
            "<rss><channel><item>"
            f"<link>{self.url}</link><description>{description}</description>"
            f"<pubDate>{format_datetime(datetime.now(timezone.utc))}</pubDate>"
            "</item></channel></rss>"
        )
        pipeline = make_region_pipeline(
            logger_name="test_region",
            region_slug="us",
            region_label="U.S. News",
            rss_feed_url="https://example.invalid/rss",
            link=link_us_article,
        )

        fixture = FixtureFetcher(feeds={"https://example.invalid/rss": rss_xml}, articles={self.url: _result(self.url)})
        with use_fetcher(fixture), patch("builtins.print"):
            saved = pipeline["get_news_from_rss"](max_age_days=3)

        self.assertEqual(saved, 1)
        (row,) = self._article_state()
        self.assertEqual(row["summary"], description)
        self.assertEqual(len(_rows("SELECT 1 FROM us_news_articles")), 1)

    def test_mixed_orchestration_saves_search_page_link_and_direct_article(self):
        search_url = "https://www.cnbc.com/finance/"
        listed_url, direct_url = _recent_url("listed"), _recent_url("direct")
        jobs = [
            {"url": search_url, "source_type": "search"},
            {"url": direct_url, "source_type": "article"},
        ]
        page = {
            "request_url": search_url, "url": search_url, "success": True, "status": 200,
            "links": [{"href": listed_url, "text": "listed"}],
        }
        fixture = FixtureFetcher(
            pages={search_url: page},
            articles={url: _result(url) for url in (listed_url, direct_url)},
        )

        with use_fetcher(fixture):
            counts = run_mixed_job_orchestration(
                jobs=jobs,
                logger=LOGGER,
                entity_from_job=lambda job: self.sector,
                candidate_links_for=lambda page, job: page["links"],
                by_source_type=True,
            )

        self.assertEqual(dict(counts), {(self.sector_id, "search"): 1, (self.sector_id, "article"): 1})
        self.assertEqual(fixture.requested, [search_url, listed_url, direct_url])

    def test_missing_page_or_article_saves_nothing_and_records_no_failure(self):
        # A crashed backend yields absent results; only an explicit success=False result may enter failed_urls.
        search_url = "https://www.cnbc.com/finance/"
        jobs = [
            {"url": search_url, "source_type": "search"},
            {"url": self.url, "source_type": "article"},
        ]

        with use_fetcher(FixtureFetcher()):
            counts = run_mixed_job_orchestration(
                jobs=jobs,
                logger=LOGGER,
                entity_from_job=lambda job: self.sector,
                candidate_links_for=lambda page, job: page["links"],
            )

        self.assertEqual(sum(counts.values()), 0)
        self.assertEqual(_rows("SELECT 1 FROM news_articles"), [])
        self.assertEqual(_rows("SELECT 1 FROM failed_urls"), [])


if __name__ == "__main__":
    unittest.main()
