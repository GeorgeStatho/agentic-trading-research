from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
WEBSCRAPING_DIR = ROOT_DIR / "Python Scripts" / "Webscraping"
if str(WEBSCRAPING_DIR) not in sys.path:
    sys.path.insert(0, str(WEBSCRAPING_DIR))

from discovery import rss
from discovery.rss import extract_recent_rss_urls
from fetching.http_client import HttpClient, HttpFetchError
from pipelines import macroNewsPipeline
from sources.registry import _policy_from_metadata
from processing.validation import validate_article_content
from sources.source_config import get_max_article_age_days, get_source_metadata


class WebscrapingCoreTests(unittest.TestCase):
    def test_http_client_retries_a_transient_response(self):
        class Response:
            def __init__(self, status_code: int):
                self.status_code = status_code
                self.url = "https://example.test/article"
                self.text = "response"
                self.headers = {}

        class Session:
            def __init__(self):
                self.responses = [Response(503), Response(200)]
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return self.responses.pop(0)

        session = Session()
        result = HttpClient(session=session, max_retries=1, backoff_seconds=0).get("https://example.test")

        self.assertTrue(result.success)
        self.assertEqual(session.calls, 2)

    def test_source_policy_prefers_the_most_specific_domain(self):
        metadata = get_source_metadata("https://markets.businessinsider.com/news?q=ai")

        self.assertEqual(metadata["domain"], "markets.businessinsider.com")
        self.assertEqual(metadata["source_type"], "market_news_portal")
        self.assertTrue(metadata["search_supported"])

    def test_modern_source_capabilities_do_not_fall_back_to_legacy_fields(self):
        policy = _policy_from_metadata(
            "example.test",
            {
                "allowed": True,
                "supports": {"search": True},
                "article_patterns": ["/article/"],
            },
        )

        self.assertTrue(policy.supports("search"))
        self.assertFalse(policy.supports("article"))

    def test_unknown_source_metadata_retains_its_hostname(self):
        metadata = get_source_metadata("https://news.example.test/article")

        self.assertEqual(metadata["domain"], "news.example.test")
        self.assertFalse(metadata["allowed"])
        self.assertEqual(
            get_max_article_age_days("https://news.example.test/article", default_days=30),
            14,
        )

    def test_malformed_rss_is_an_empty_discovery_result(self):
        self.assertEqual(extract_recent_rss_urls("<rss><channel>"), [])

    def test_rss_discovery_deduplicates_recent_links(self):
        published_at = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml = f"""
            <rss><channel>
              <item><link>https://www.cnbc.com/2026/07/27/example.html</link><pubDate>{published_at}</pubDate></item>
              <item><link>https://www.cnbc.com/2026/07/27/example.html</link><pubDate>{published_at}</pubDate></item>
            </channel></rss>
        """

        self.assertEqual(
            extract_recent_rss_urls(xml),
            ["https://www.cnbc.com/2026/07/27/example.html"],
        )

    def test_unavailable_rss_feed_is_contained(self):
        with patch.object(rss, "fetch_rss_feed_xml", side_effect=HttpFetchError("network unavailable")):
            self.assertEqual(rss.fetch_recent_rss_urls("https://feed.example.test/rss"), [])

    def test_macro_fetch_failure_uses_its_empty_fallback(self):
        with patch.object(macroNewsPipeline, "_fetch_json", side_effect=HttpFetchError("network unavailable")):
            self.assertEqual(macroNewsPipeline.fetch_forex_factory_macro_calendar(), [])

    def test_article_validation_rejects_short_and_blocked_content(self):
        self.assertFalse(validate_article_content(title="Title", body="Too short").is_valid)
        self.assertFalse(
            validate_article_content(
                title="Article",
                body=("Useful-looking text. " * 20) + "Please enable cookies to continue.",
            ).is_valid
        )
        self.assertTrue(
            validate_article_content(
                title="Article",
                body="Useful reporting. " * 20,
                minimum_body_chars=100,
            ).is_valid
        )
        self.assertTrue(
            validate_article_content(
                title="Cybersecurity report",
                body=("Detailed reporting about CAPTCHA bypasses. " * 30),
                minimum_body_chars=100,
            ).is_valid
        )


if __name__ == "__main__":
    unittest.main()
