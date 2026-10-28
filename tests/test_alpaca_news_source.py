import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from APIs import alpacaNewsAPI
from engine import page_fetch
from engine.page_fetch import LiveFetcher, _extract_article_result_from_api_payload
from pipelines import companyNewsPipeline
from news_db_modules.provenance import _origin
from sources.source_config import get_source_metadata, is_allowed_source

BENZINGA_URL = "https://www.benzinga.com/news/25/10/123/nvidia-rises"
COMPANY = {
    "id": 7,
    "symbol": "NVDA",
    "name": "NVIDIA Corporation",
    "industry_key": "semis",
    "industry_name": "Semiconductors",
    "sector_key": "tech",
    "sector_name": "Technology",
}


def _news(n=1, *, content="<p>Revenue rose</p><p>Shares up</p>", summary="A summary"):
    return SimpleNamespace(
        id=n,
        headline=f"Headline {n}",
        source="benzinga",
        url=f"https://www.benzinga.com/news/25/10/{n}/story-{n}",
        summary=summary,
        created_at=datetime(2025, 10, 1, 12, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 10, 1, 12, 5, tzinfo=timezone.utc),
        symbols=["NVDA"],
        author="Jane Doe",
        content=content,
        images=[],
    )


def _client(items):
    # Mirrors NewsClient.get_news returning a NewsSet, whose items live in .data["news"].
    client = MagicMock()
    client.get_news.return_value = SimpleNamespace(data={"news": list(items)}, next_page_token=None)
    return client


def _article(url):
    payload = alpacaNewsAPI.get_article_content(url)
    return _extract_article_result_from_api_payload(payload, url)


class GetStockDataTests(unittest.TestCase):
    def test_sends_one_request_and_returns_pairs(self):
        items = [_news(1), _news(2)]
        client = _client(items)
        with patch.object(alpacaNewsAPI, "_get_news_client", return_value=client):
            payload = alpacaNewsAPI.get_stock_data("NVDA")

        client.get_news.assert_called_once()
        request = client.get_news.call_args.args[0]
        self.assertEqual(request.symbols, "NVDA")
        self.assertEqual(request.limit, 10)
        self.assertTrue(request.include_content)
        self.assertTrue(request.exclude_contentless)
        self.assertEqual(
            companyNewsPipeline._extract_api_news_pairs(payload),
            [(item.headline, item.url) for item in items],
        )

    def test_dict_return_type_is_handled(self):
        client = MagicMock()
        client.get_news.return_value = {"news": [_news(3)]}
        with patch.object(alpacaNewsAPI, "_get_news_client", return_value=client):
            payload = alpacaNewsAPI.get_stock_data("NVDA")
        self.assertEqual(len(companyNewsPipeline._extract_api_news_pairs(payload)), 1)

    def test_api_exception_propagates(self):
        client = MagicMock()
        client.get_news.side_effect = RuntimeError("boom")
        with patch.object(alpacaNewsAPI, "_get_news_client", return_value=client):
            with self.assertRaises(RuntimeError):
                alpacaNewsAPI.get_stock_data("NVDA")


class GetArticleContentTests(unittest.TestCase):
    def test_html_is_stripped_and_fields_are_set(self):
        item = _news(10)
        with patch.object(alpacaNewsAPI, "_get_news_client", return_value=_client([item])):
            alpacaNewsAPI.get_stock_data("NVDA")

        result = _article(item.url)
        self.assertTrue(result.success)
        self.assertEqual(result.title, item.headline)
        self.assertIn("Revenue rose", result.text)
        self.assertIn("Shares up", result.text)
        self.assertNotIn("<p>", result.text)
        self.assertTrue(result.published_at.startswith("2025-10-01T12:00:00"))

        data = alpacaNewsAPI.get_article_content(item.url)["data"]
        self.assertEqual(data["symbols"], ["NVDA"])
        self.assertEqual(data["source"], "benzinga")
        self.assertEqual(data["author"], "Jane Doe")

    def test_empty_content_falls_back_to_summary(self):
        item = _news(11, content="", summary="Fallback summary text")
        with patch.object(alpacaNewsAPI, "_get_news_client", return_value=_client([item])):
            alpacaNewsAPI.get_stock_data("NVDA")
        result = _article(item.url)
        self.assertTrue(result.success)
        self.assertEqual(result.text, "Fallback summary text")

    def test_unknown_url_is_unavailable_not_empty(self):
        # A cache miss is "missing" (retried next run), never a failed article that gets blacklisted.
        url = "https://www.benzinga.com/news/25/10/999/never-seen"
        with self.assertRaises(page_fetch.ArticleContentUnavailable):
            alpacaNewsAPI.get_article_content(url)

    def test_cache_holds_a_full_run_of_companies(self):
        # A run builds every company's jobs before fetching any article; 100 companies x 10 items must fit.
        self.assertGreaterEqual(alpacaNewsAPI.CONTENT_CACHE_LIMIT, 1000)

    def test_cache_is_bounded_to_the_most_recent_entries(self):
        limit = alpacaNewsAPI.CONTENT_CACHE_LIMIT
        items = [_news(10_000 + i) for i in range(limit + 100)]
        with patch.object(alpacaNewsAPI, "_get_news_client", return_value=_client(items)):
            alpacaNewsAPI.get_stock_data("NVDA")
        with self.assertRaises(page_fetch.ArticleContentUnavailable):
            alpacaNewsAPI.get_article_content(items[0].url)
        self.assertEqual(alpacaNewsAPI.get_article_content(items[-1].url)["data"]["title"], items[-1].headline)
        self.assertEqual(alpacaNewsAPI.get_article_content(items[100].url)["data"]["title"], items[100].headline)


class MissingCredentialsTests(unittest.TestCase):
    def test_client_getter_returns_none_without_credentials(self):
        env = {k: v for k, v in os.environ.items() if k not in ("PUBLIC_KEY", "PRIVATE_KEY")}
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(alpacaNewsAPI._get_news_client())

    def test_missing_client_gives_no_articles_or_raises(self):
        with patch.object(alpacaNewsAPI, "_get_news_client", return_value=None):
            try:
                payload = alpacaNewsAPI.get_stock_data("NVDA")
            except Exception:
                return
        self.assertEqual(companyNewsPipeline._extract_api_news_pairs(payload), [])

    def test_build_api_jobs_keeps_other_sources_working(self):
        other = lambda symbol, limit=10: {
            "data": {"articles": [{"title": "Fool take", "url": "https://www.fool.com/investing/2025/10/01/x/"}]}
        }
        sources = tuple(
            (name, fn if name == "alpaca_news_api" else other)
            for name, fn in companyNewsPipeline.API_SOURCES
        )
        names = [name for name, _ in sources]
        self.assertIn("alpaca_news_api", names)
        with patch.object(companyNewsPipeline, "API_SOURCES", sources), \
                patch.object(alpacaNewsAPI, "_get_news_client", return_value=None):
            jobs = companyNewsPipeline._build_api_jobs(COMPANY)
        self.assertTrue(jobs)
        self.assertNotIn("alpaca_news_api", {job["source_name"] for job in jobs})


class RegistrationTests(unittest.TestCase):
    def test_source_registered_in_pipeline_and_fetchers(self):
        self.assertIn(("alpaca_news_api", alpacaNewsAPI.get_stock_data), companyNewsPipeline.API_SOURCES)
        self.assertIn(("benzinga.com", alpacaNewsAPI.get_article_content), page_fetch.ARTICLE_CONTENT_FETCHERS)

    def test_alpaca_articles_become_company_jobs(self):
        item = _news(20)
        sources = (("alpaca_news_api", alpacaNewsAPI.get_stock_data),)
        with patch.object(companyNewsPipeline, "API_SOURCES", sources), \
                patch.object(alpacaNewsAPI, "_get_news_client", return_value=_client([item])):
            jobs = companyNewsPipeline._build_api_jobs(COMPANY)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], item.url)
        self.assertEqual(jobs[0]["source_name"], "alpaca_news_api")
        self.assertEqual(jobs[0]["company"]["id"], COMPANY["id"])

    def test_benzinga_source_config(self):
        self.assertTrue(is_allowed_source(BENZINGA_URL))
        # Benzinga republishes and auto-writes; it is not a wire, so it must not count as an independent root.
        self.assertEqual(get_source_metadata(BENZINGA_URL)["provenance_source_type"], "aggregator")

    def test_benzinga_roots_are_unverified_unless_a_wire_is_named(self):
        article = {"source_url": BENZINGA_URL, "source": "benzinga", "summary": "Shares rose.", "body": "Shares rose."}
        self.assertEqual(_origin(article), ("aggregator", "unverified", None))
        release = {**article, "body": "SANTA CLARA, Calif.--(BUSINESS WIRE)--NVIDIA today announced results."}
        self.assertEqual(_origin(release), ("wire_service", "wire:businesswire", None))

    def test_unknown_benzinga_url_is_missing_and_not_crawled(self):
        with patch.object(page_fetch, "crawl_article_pages") as crawl:
            result = LiveFetcher().articles([BENZINGA_URL])
        crawl.assert_not_called()
        self.assertNotIn(BENZINGA_URL, result)

    def test_unavailable_content_from_any_api_fetcher_is_missing(self):
        def unavailable(url):
            raise page_fetch.ArticleContentUnavailable(url)
        url = "https://www.fool.com/investing/2025/10/01/x/"
        with patch.object(page_fetch, "ARTICLE_CONTENT_FETCHERS", (("fool.com", unavailable),)), \
                patch.object(page_fetch, "crawl_article_pages") as crawl:
            result = LiveFetcher().articles([url])
        crawl.assert_not_called()
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
