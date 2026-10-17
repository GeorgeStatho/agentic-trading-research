from __future__ import annotations

import unittest
from unittest.mock import patch

from scrapy.http import HtmlResponse

from engine import article_scraper
from engine.article_extraction import ArticleExtractionResult, RenderedPageExtraction

URL = "https://www.cnbc.com/search/?query=aapl"
FINAL_URL = "https://www.cnbc.com/search/?query=aapl&page=2"


class _FakeQueue:
    def __init__(self, payload: dict | None = None):
        self._payload = payload

    def empty(self) -> bool:
        return self._payload is None

    def get(self) -> dict:
        assert self._payload is not None, "queue.get() would block forever"
        return self._payload


class _FakeWorker:
    """Stands in for a spawned child that has already exited."""

    def __init__(self, exitcode: int = 0):
        self.exitcode = exitcode

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        pass


class _FakeContext:
    def __init__(self, payload: dict | None = None, exitcode: int = 0):
        self.queue = _FakeQueue(payload)
        self.worker = _FakeWorker(exitcode)
        self.process_args = None

    def SimpleQueue(self) -> _FakeQueue:
        return self.queue

    def Process(self, target, args) -> _FakeWorker:
        self.process_args = args
        return self.worker


class ItemBuilderTests(unittest.TestCase):
    def test_rendered_item_keeps_request_url_when_response_has_no_request(self):
        response = HtmlResponse(url=FINAL_URL, body=b"<html></html>", encoding="utf-8")
        self.assertIsNone(response.request)
        rendered = RenderedPageExtraction(
            request_url=URL,
            page_url=FINAL_URL,
            status=200,
            response=response,
            article=ArticleExtractionResult(url=FINAL_URL, success=True),
        )

        item = article_scraper._build_item_from_rendered_extraction(rendered)

        self.assertEqual(item["request_url"], URL)

    def test_scraped_item_keeps_request_url_when_response_has_no_request(self):
        response = HtmlResponse(url=FINAL_URL, body=b"<html></html>", encoding="utf-8")
        result = ArticleExtractionResult(url=FINAL_URL, success=True)

        with patch.object(article_scraper, "extract_from_response", return_value=result):
            item = article_scraper._build_scraped_item(response, request_url=URL)

        self.assertEqual(item["request_url"], URL)


class CrawlArticlesFallbackTests(unittest.TestCase):
    def test_non_runtime_playwright_error_falls_back_to_scrapy(self):
        urls = [URL, "https://example.com/story"]
        with (
            patch.object(article_scraper, "should_use_playwright_for_source_url", side_effect=lambda u: "cnbc" in u),
            patch.object(article_scraper, "fetch_rendered_pages", side_effect=ValueError("boom")),
            patch.object(article_scraper, "_run_crawl", return_value=[{"request_url": URL}]) as run_crawl,
        ):
            items = article_scraper.crawl_articles(urls)

        self.assertEqual(items, [{"request_url": URL}])
        self.assertEqual(run_crawl.call_args.args[0], urls)

    def test_keyboard_interrupt_is_not_swallowed(self):
        with (
            patch.object(article_scraper, "should_use_playwright_for_source_url", return_value=True),
            patch.object(article_scraper, "fetch_rendered_pages", side_effect=KeyboardInterrupt),
            patch.object(article_scraper, "_run_crawl") as run_crawl,
        ):
            with self.assertRaises(KeyboardInterrupt):
                article_scraper.crawl_articles([URL])

        run_crawl.assert_not_called()


class CrawlArticlePagesTests(unittest.TestCase):
    def _playwright_backend(self):
        return (
            patch.object(article_scraper, "should_use_playwright_backend", return_value=True),
            patch.object(article_scraper, "should_use_playwright_for_url", side_effect=lambda u: "cnbc" in u),
        )

    def test_playwright_failure_sends_urls_to_scrapy(self):
        urls = [URL, "https://example.com/story"]
        ctx = _FakeContext({"items": [{"request_url": URL, "url": URL, "success": True}]})
        on, domains = self._playwright_backend()
        with (
            on,
            domains,
            patch.object(article_scraper, "fetch_rendered_pages", side_effect=OSError("no browser")),
            patch.object(article_scraper.multiprocessing, "get_context", return_value=ctx),
        ):
            results = article_scraper.crawl_article_pages(urls)

        self.assertEqual(ctx.process_args[0], urls)
        self.assertIn(URL, results)

    def test_playwright_results_are_kept_alongside_scrapy_results(self):
        playwright_item = {"request_url": URL, "url": URL, "success": True}
        ctx = _FakeContext({"items": [{"request_url": "https://example.com/story", "url": "https://example.com/story"}]})
        on, domains = self._playwright_backend()
        with (
            on,
            domains,
            patch.object(article_scraper, "_crawl_with_playwright", return_value=[playwright_item]),
            patch.object(article_scraper.multiprocessing, "get_context", return_value=ctx),
        ):
            results = article_scraper.crawl_article_pages([URL, "https://example.com/story"])

        self.assertEqual(ctx.process_args[0], ["https://example.com/story"])
        self.assertEqual(set(results), {URL, "https://example.com/story"})

    def test_worker_that_dies_without_result_raises_instead_of_hanging(self):
        ctx = _FakeContext(payload=None, exitcode=-9)
        with patch.object(article_scraper.multiprocessing, "get_context", return_value=ctx):
            with self.assertRaisesRegex(RuntimeError, "-9"):
                article_scraper.crawl_article_pages(["https://example.com/story"])

    def test_worker_error_payload_still_raises(self):
        ctx = _FakeContext({"error": "kaput"}, exitcode=0)
        with patch.object(article_scraper.multiprocessing, "get_context", return_value=ctx):
            with self.assertRaisesRegex(RuntimeError, "kaput"):
                article_scraper.crawl_article_pages(["https://example.com/story"])


if __name__ == "__main__":
    unittest.main()
