from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import unittest
from unittest.mock import patch

from tests.db_fixtures import temporary_database
from engine.page_fetch import FixtureFetcher, use_fetcher
from news_db_modules.links import link_us_article
from pipelines._regionNewsPipeline import make_region_pipeline
from pipelines._sector_rss import extract_recent_rss_items, get_recent_sector_feed_items
from pipelines.job_builder import build_sector_rss_jobs


def _rss(*items: str) -> str:
    return f"<rss><channel>{''.join(items)}</channel></rss>"


def _item(link: str | None, *, age_days: float = 0, description: str | None = None) -> str:
    pub_date = format_datetime(datetime.now(timezone.utc) - timedelta(days=age_days))
    link_xml = f"<link>{link}</link>" if link is not None else ""
    description_xml = f"<description>{description}</description>" if description is not None else ""
    return f"<item>{link_xml}<pubDate>{pub_date}</pubDate>{description_xml}</item>"


class ExtractRecentRssItemsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)

    def test_description_is_html_decoded_and_stripped_to_text(self):
        rss_xml = _rss(_item("https://www.cnbc.com/a", description="&lt;p&gt;A &amp;amp; B&lt;/p&gt;"))

        self.assertEqual(extract_recent_rss_items(rss_xml), [("https://www.cnbc.com/a", "A & B")])

    def test_missing_description_is_none(self):
        rss_xml = _rss(_item("https://www.cnbc.com/a"))

        self.assertEqual(extract_recent_rss_items(rss_xml), [("https://www.cnbc.com/a", None)])

    def test_item_older_than_max_age_is_dropped(self):
        rss_xml = _rss(_item("https://www.cnbc.com/old", age_days=10), _item("https://www.cnbc.com/new"))

        self.assertEqual(extract_recent_rss_items(rss_xml), [("https://www.cnbc.com/new", None)])

    def test_item_without_link_is_dropped(self):
        rss_xml = _rss(_item(None), _item("https://www.cnbc.com/a"))

        self.assertEqual(extract_recent_rss_items(rss_xml), [("https://www.cnbc.com/a", None)])

    def test_empty_or_unparseable_xml_returns_empty_list(self):
        for bad in (None, "", "<bad"):
            with self.subTest(bad=bad):
                self.assertEqual(extract_recent_rss_items(bad), [])


class FeedFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)

    def test_missing_feed_does_not_stop_other_feeds(self):
        down, up = "https://example.invalid/down", "https://example.invalid/up"
        fixture = FixtureFetcher(feeds={up: _rss(_item("https://www.cnbc.com/a"))})

        with patch("pipelines._sector_rss.get_sector_feed_urls", return_value=[down, up]), use_fetcher(fixture):
            items = get_recent_sector_feed_items({"sector_key": "technology"})

        self.assertEqual(items, [("https://www.cnbc.com/a", None)])
        self.assertEqual(fixture.requested, [down, up])

    def test_region_rss_with_no_feed_saves_zero(self):
        pipeline = make_region_pipeline(
            logger_name="test_region",
            region_slug="us",
            region_label="U.S. News",
            rss_feed_url="https://example.invalid/rss",
            link=link_us_article,
        )

        with use_fetcher(FixtureFetcher()), patch("builtins.print"):
            self.assertEqual(pipeline["get_news_from_rss"](), 0)


class BuildSectorRssJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)

    def test_dedupes_urls_and_carries_summary(self):
        sector = {"sector_key": "technology", "name": "Technology"}
        items = [
            ("https://www.cnbc.com/a", "First summary"),
            ("https://www.cnbc.com/a", None),
            ("https://www.cnbc.com/b", None),
        ]

        with patch("pipelines.job_builder.supports_source_type", return_value=True):
            jobs = build_sector_rss_jobs(sector, items)

        self.assertEqual(
            [(job["url"], job["summary"]) for job in jobs],
            [("https://www.cnbc.com/a", "First summary"), ("https://www.cnbc.com/b", None)],
        )
        self.assertEqual(jobs[0]["sector"], sector)


if __name__ == "__main__":
    unittest.main()
