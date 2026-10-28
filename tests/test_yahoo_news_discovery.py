from __future__ import annotations

import json
import unittest
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from db_common import get_connection
from engine.article_extraction import ArticleExtractionResult
from engine.page_fetch import FixtureFetcher, use_fetcher
from market_data import yFinanceNews
from market_db_modules.entities import add_company, add_industry, add_sector
from news_db_modules.provenance import _origin
from pipelines import companyNewsPipeline
from sources.source_config import is_allowed_source
from tests.db_fixtures import temporary_database

YAHOO_URL = "https://finance.yahoo.com/news/nvidia-announces-results-123456.html"
FLAT_ITEMS = [
    {"title": "Nvidia announces results", "link": YAHOO_URL, "publisher": "Business Wire", "uuid": "1",
     "relatedTickers": ["NVDA"]},
    {"title": "Blocked story", "link": "https://example-blocked.test/story", "publisher": "Nobody", "uuid": "2",
     "relatedTickers": ["NVDA", "AMD"]},
    {"title": "No link", "publisher": "Reuters", "uuid": "3", "relatedTickers": ["NVDA"]},
    {"link": YAHOO_URL + "?x=1", "publisher": "Reuters", "uuid": "4", "relatedTickers": ["NVDA"]},
]
NESTED_ITEMS = [
    {
        "content": {
            "title": "Nested story",
            "clickThroughUrl": {"url": YAHOO_URL},
            "provider": {"displayName": "GlobeNewswire"},
        }
    },
    {"content": {"title": "Nested canonical", "canonicalUrl": {"url": YAHOO_URL + "?c=1"}}},
    {"content": {"title": "Nested blocked", "previewUrl": "https://example-blocked.test/a"}},
]


def _yf(*, search=None, search_error=None, ticker=None, ticker_error=None):
    """A stand-in `yfinance` module: Search(...).news and Ticker(...).get_news(...)."""
    yf = MagicMock()
    if search_error is not None:
        yf.Search.side_effect = search_error
    else:
        yf.Search.return_value.news = search
    if ticker_error is not None:
        yf.Ticker.return_value.get_news.side_effect = ticker_error
    else:
        yf.Ticker.return_value.get_news.return_value = ticker
    return yf


class GetCompanyNewsItemsTests(unittest.TestCase):
    def _run(self, yf):
        with patch.object(yFinanceNews, "yf", yf):
            return yFinanceNews.get_company_news_items("NVDA", count=7)

    def test_search_results_are_used(self):
        yf = _yf(search=FLAT_ITEMS, ticker=NESTED_ITEMS)
        self.assertEqual(self._run(yf), FLAT_ITEMS)
        yf.Search.assert_called_once_with("NVDA", news_count=7)
        yf.Ticker.return_value.get_news.assert_not_called()

    def test_search_items_not_tagged_with_the_ticker_are_dropped(self):
        # Search is a free-text query; for short tickers ("F", "ON") it returns unrelated news.
        items = [
            {"title": "Ford story", "link": YAHOO_URL, "relatedTickers": ["F", "GM"]},
            {"title": "Unrelated", "link": YAHOO_URL + "?u=1", "relatedTickers": ["FDX"]},
            {"title": "Untagged", "link": YAHOO_URL + "?u=2"},
        ]
        with patch.object(yFinanceNews, "yf", _yf(search=items, ticker=[])):
            self.assertEqual([i["title"] for i in yFinanceNews.get_company_news_items("f", count=7)], ["Ford story"])

    def test_share_class_symbols_match_yahoo_dash_form(self):
        items = [{"title": "Berkshire", "link": YAHOO_URL, "relatedTickers": ["BRK-B"]}]
        with patch.object(yFinanceNews, "yf", _yf(search=items, ticker=[])):
            self.assertEqual(len(yFinanceNews.get_company_news_items("BRK.B")), 1)

    def test_search_with_no_tagged_items_falls_back_to_ticker_news(self):
        items = [{"title": "Unrelated", "link": YAHOO_URL, "relatedTickers": ["FDX"]}]
        yf = _yf(search=items, ticker=NESTED_ITEMS)
        self.assertEqual(self._run(yf), NESTED_ITEMS)

    def test_empty_search_falls_back_to_ticker_news(self):
        yf = _yf(search=[], ticker=NESTED_ITEMS)
        self.assertEqual(self._run(yf), NESTED_ITEMS)
        yf.Ticker.assert_called_once_with("NVDA")
        yf.Ticker.return_value.get_news.assert_called_once_with(count=7)

    def test_search_error_falls_back_to_ticker_news(self):
        yf = _yf(search_error=RuntimeError("search down"), ticker=NESTED_ITEMS)
        self.assertEqual(self._run(yf), NESTED_ITEMS)

    def test_both_failing_raises_the_fallback_error(self):
        yf = _yf(search_error=RuntimeError("search down"), ticker_error=ValueError("ticker down"))
        with self.assertRaisesRegex(ValueError, "ticker down"):
            self._run(yf)


class ExtractTests(unittest.TestCase):
    def test_flat_items_keep_allowed_urls_and_drop_the_rest(self):
        pairs = yFinanceNews.extract_title_and_url(FLAT_ITEMS)
        self.assertEqual(pairs, [("Nvidia announces results", YAHOO_URL)])

    def test_nested_items_still_parse(self):
        pairs = yFinanceNews.extract_title_and_url(NESTED_ITEMS)
        self.assertEqual(pairs, [("Nested story", YAHOO_URL), ("Nested canonical", YAHOO_URL + "?c=1")])

    def test_publisher_is_returned_for_both_shapes(self):
        self.assertEqual(
            yFinanceNews.extract_title_url_publisher(FLAT_ITEMS),
            [("Nvidia announces results", YAHOO_URL, "Business Wire")],
        )
        self.assertEqual(
            yFinanceNews.extract_title_url_publisher(NESTED_ITEMS),
            [("Nested story", YAHOO_URL, "GlobeNewswire"), ("Nested canonical", YAHOO_URL + "?c=1", "")],
        )

    def test_missing_publisher_is_an_empty_string(self):
        flat = [{"title": "T", "link": YAHOO_URL}]
        self.assertEqual(yFinanceNews.extract_title_url_publisher(flat), [("T", YAHOO_URL, "")])


class SourceConfigTests(unittest.TestCase):
    def test_finance_yahoo_is_allowed_but_news_yahoo_is_not(self):
        self.assertTrue(is_allowed_source(YAHOO_URL))
        self.assertFalse(is_allowed_source("https://news.yahoo.com/some-story-123.html"))


class OriginTests(unittest.TestCase):
    def _origin(self, publisher, **overrides):
        article = {
            "source_url": YAHOO_URL,
            "source": "finance.yahoo.com",
            "summary": "Quarterly results",
            "body": "Nvidia reported record revenue.",
            "raw_json": json.dumps({"provenance": {"publisher": publisher}}),
            **overrides,
        }
        return _origin(article)

    def test_business_wire_publisher_is_credited(self):
        self.assertEqual(self._origin("Business Wire"), ("wire_service", "wire:businesswire", None))

    def test_globenewswire_publisher_is_credited(self):
        self.assertEqual(self._origin("GlobeNewswire"), ("wire_service", "wire:globenewswire", None))
        self.assertEqual(self._origin("Globe Newswire"), ("wire_service", "wire:globenewswire", None))

    def test_globenewswire_in_body_is_credited(self):
        self.assertEqual(
            self._origin("", body="TORONTO -- (GLOBE NEWSWIRE) -- Results."),
            ("wire_service", "wire:globenewswire", None),
        )

    def test_non_wire_publisher_stays_unverified(self):
        self.assertEqual(self._origin("Simply Wall St"), ("aggregator", "unverified", None))


class PublisherReachesSavedArticleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)
        sector_id = add_sector("technology", "Technology")
        industry_id = add_industry(sector_id, "semiconductors", "Semiconductors")
        add_company(industry_id, "NVDA", "NVIDIA Corporation")
        self.company = next(c for c in companyNewsPipeline.get_all_companies() if c["symbol"] == "NVDA")

    def test_publisher_is_stored_under_raw_json_provenance(self):
        item = {"title": "Nvidia announces results", "link": YAHOO_URL, "publisher": "Business Wire"}
        article = ArticleExtractionResult(
            url=YAHOO_URL,
            title="Nvidia announces results",
            text="NVIDIA Corporation reported record revenue. " * 30,
            published_at=datetime.now(timezone.utc).isoformat(),
            success=True,
        )
        with (
            patch.object(companyNewsPipeline, "get_company_news_items", return_value=[item]),
            patch.object(companyNewsPipeline, "_build_api_jobs", return_value=[]),
            use_fetcher(FixtureFetcher(articles={YAHOO_URL: article})),
        ):
            jobs = [j for j in companyNewsPipeline._build_company_jobs(self.company) if j["url"] == YAHOO_URL]
            self.assertEqual(len(jobs), 1)
            companyNewsPipeline._process_company_jobs(jobs)

        with closing(get_connection()) as conn:
            (row,) = conn.execute(
                "SELECT c.raw_json FROM news_articles a JOIN news_article_content c ON c.article_id = a.id"
            ).fetchall()
        raw = json.loads(row["raw_json"])
        self.assertEqual(raw["provenance"], {"publisher": "Business Wire"})
        self.assertEqual(raw["symbol"], "NVDA")  # entity identity is still merged in


if __name__ == "__main__":
    unittest.main()
