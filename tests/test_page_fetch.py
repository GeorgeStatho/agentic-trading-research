import threading
import unittest
from unittest.mock import MagicMock, patch

from engine import page_fetch
from engine.article_extraction import ArticleExtractionResult
from engine.page_fetch import (
    FixtureFetcher,
    LiveFetcher,
    current_fetcher,
    fetch_articles,
    fetch_feed,
    fetch_source_pages,
    use_fetcher,
)

FOOL_URL = "https://investor.example.com/a"
OTHER_URL = "https://example.com/b"
GOOD_PAYLOAD = {"data": {"title": "T", "body": "B", "published_at": "2026-01-01", "url": FOOL_URL}}


def _fetchers(fn):
    return (("investor.example.com", fn),)


class LiveArticlesTests(unittest.TestCase):
    def test_api_success_skips_crawler(self):
        with patch.object(page_fetch, "ARTICLE_CONTENT_FETCHERS", _fetchers(lambda url: GOOD_PAYLOAD)), \
                patch.object(page_fetch, "crawl_article_pages") as crawl:
            result = LiveFetcher().articles([FOOL_URL])
        self.assertTrue(result[FOOL_URL].success)
        self.assertEqual(result[FOOL_URL].text, "B")
        crawl.assert_not_called()

    def test_api_raises_falls_back_to_crawler(self):
        crawled = ArticleExtractionResult(url=FOOL_URL, success=True)
        with patch.object(page_fetch, "ARTICLE_CONTENT_FETCHERS", _fetchers(MagicMock(side_effect=ValueError("x")))), \
                patch.object(page_fetch, "crawl_article_pages", return_value={FOOL_URL: crawled}) as crawl:
            result = LiveFetcher().articles([FOOL_URL])
        crawl.assert_called_once_with([FOOL_URL])
        self.assertIs(result[FOOL_URL], crawled)

    def test_api_bad_payload_is_failure_without_crawler(self):
        with patch.object(page_fetch, "ARTICLE_CONTENT_FETCHERS", _fetchers(lambda url: {"data": None})), \
                patch.object(page_fetch, "crawl_article_pages") as crawl:
            result = LiveFetcher().articles([FOOL_URL])
        self.assertFalse(result[FOOL_URL].success)
        crawl.assert_not_called()

    def test_crawler_error_drops_its_urls_and_keeps_api_results(self):
        with patch.object(page_fetch, "ARTICLE_CONTENT_FETCHERS", _fetchers(lambda url: GOOD_PAYLOAD)), \
                patch.object(page_fetch, "crawl_article_pages", side_effect=RuntimeError("boom")):
            result = LiveFetcher().articles([FOOL_URL, OTHER_URL])
        self.assertEqual(set(result), {FOOL_URL})

    def test_api_fetchers_run_at_the_same_time(self):
        # One at a time, the first fetch would wait alone at the barrier until it times out.
        barrier = threading.Barrier(3, timeout=2)
        urls = [f"https://investor.example.com/{n}" for n in range(3)]

        def waiting(url):
            barrier.wait()
            return {"data": {**GOOD_PAYLOAD["data"], "url": url}}

        with patch.object(page_fetch, "ARTICLE_CONTENT_FETCHERS", _fetchers(waiting)), \
                patch.object(page_fetch, "crawl_article_pages") as crawl:
            result = LiveFetcher().articles(urls)
        self.assertEqual(list(result), urls)
        self.assertTrue(all(item.success for item in result.values()))
        crawl.assert_not_called()

    def test_parallel_fetch_still_sends_a_failed_api_url_to_the_crawler(self):
        failing = "https://investor.example.com/broken"

        def fetcher(url):
            if url == failing:
                raise ValueError("x")
            return GOOD_PAYLOAD

        crawled = ArticleExtractionResult(url=failing, success=True)
        with patch.object(page_fetch, "ARTICLE_CONTENT_FETCHERS", _fetchers(fetcher)), \
                patch.object(page_fetch, "crawl_article_pages", return_value={failing: crawled}) as crawl:
            result = LiveFetcher().articles([FOOL_URL, failing, OTHER_URL])
        crawl.assert_called_once_with([failing, OTHER_URL])
        self.assertIs(result[failing], crawled)
        self.assertTrue(result[FOOL_URL].success)

    def test_crawler_keyboard_interrupt_propagates(self):
        with patch.object(page_fetch, "crawl_article_pages", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                LiveFetcher().articles([OTHER_URL])

    def test_empty_input_calls_no_backend(self):
        with patch.object(page_fetch, "crawl_article_pages") as pages, \
                patch.object(page_fetch, "crawl_articles") as sources:
            self.assertEqual(LiveFetcher().articles([]), {})
            self.assertEqual(LiveFetcher().source_pages([]), [])
        pages.assert_not_called()
        sources.assert_not_called()


class LiveSourcePagesAndFeedTests(unittest.TestCase):
    def test_source_pages_crawler_error_returns_empty(self):
        with patch.object(page_fetch, "crawl_articles", side_effect=RuntimeError("boom")):
            self.assertEqual(LiveFetcher().source_pages([OTHER_URL]), [])

    def test_source_pages_success(self):
        with patch.object(page_fetch, "crawl_articles", return_value=[{"url": OTHER_URL}]):
            self.assertEqual(LiveFetcher().source_pages([OTHER_URL]), [{"url": OTHER_URL}])

    def test_feed_error_returns_none(self):
        with patch.object(page_fetch, "urlopen", side_effect=OSError("down")):
            self.assertIsNone(LiveFetcher().feed("https://example.com/rss"))

    def test_feed_success_decodes_text(self):
        response = MagicMock()
        response.read.return_value = "<rss>café</rss>".encode("utf-8")
        response.__enter__.return_value = response
        with patch.object(page_fetch, "urlopen", return_value=response):
            self.assertEqual(LiveFetcher().feed("https://example.com/rss"), "<rss>café</rss>")


class FetcherSwitchTests(unittest.TestCase):
    def test_use_fetcher_routes_and_restores(self):
        live = current_fetcher()
        self.assertIsInstance(live, LiveFetcher)
        article = ArticleExtractionResult(url=OTHER_URL, success=True)
        fixture = FixtureFetcher(pages={"p": {"url": "p"}}, articles={OTHER_URL: article}, feeds={"f": "<rss/>"})
        with use_fetcher(fixture) as active:
            self.assertIs(active, fixture)
            self.assertIs(current_fetcher(), fixture)
            self.assertEqual(fetch_source_pages(["p"]), [{"url": "p"}])
            self.assertEqual(fetch_articles([OTHER_URL]), {OTHER_URL: article})
            self.assertEqual(fetch_feed("f"), "<rss/>")
        self.assertIs(current_fetcher(), live)

    def test_fixture_unknown_urls_absent_and_recorded(self):
        fixture = FixtureFetcher()
        with use_fetcher(fixture):
            self.assertEqual(fetch_source_pages(["a"]), [])
            self.assertEqual(fetch_articles(["b"]), {})
            self.assertIsNone(fetch_feed("c"))
        self.assertEqual(fixture.requested, ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
