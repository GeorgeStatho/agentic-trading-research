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
from sources.company_official import fetch_sec_edgar_events, parse_company_ir_feed
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

    def test_macro_source_failure_is_contained(self):
        with patch.object(macroNewsPipeline, "_fetch_text", side_effect=HttpFetchError("network unavailable")):
            self.assertEqual(macroNewsPipeline.fetch_macro_calendar(), [])

    def test_official_macro_adapters_normalize_bls_and_bea_schedule_rows(self):
        html_by_host = {
            "bls.gov": """
                <h1>2026 Release Schedule</h1>
                <table><tr><th>Release</th><th>Date</th></tr>
                <tr><td>Consumer Price Index</td><td>August 12, 2026 8:30 AM</td></tr></table>
            """,
            "bea.gov": """
                <h1>2026 News Release Schedule</h1>
                <table><tr><th>Release</th><th>Date</th></tr>
                <tr><td>Gross Domestic Product, 2nd Quarter 2026 (Advance Estimate)</td><td>July 30, 2026</td></tr></table>
            """,
            "federalreserve.gov": "<html></html>",
        }

        events = macroNewsPipeline.fetch_macro_calendar(
            now=datetime(2026, 7, 21, tzinfo=timezone.utc),
            fetch_text=lambda url: next(html for host, html in html_by_host.items() if host in url),
        )

        self.assertEqual(len(events), 2)
        by_source = {event["source"]: event for event in events}
        self.assertEqual(by_source["Bureau of Labor Statistics"]["category"], "inflation")
        self.assertEqual(by_source["Bureau of Labor Statistics"]["importance"], "high")
        self.assertEqual(by_source["Bureau of Economic Analysis"]["category"], "growth")
        self.assertEqual(by_source["Bureau of Economic Analysis"]["scheduled_at_utc"], "2026-07-30T12:30:00+00:00")

    def test_sec_daily_index_adapter_filters_to_tracked_high_signal_filings(self):
        ticker_map = {
            "0": {"ticker": "AAPL", "cik_str": 320193},
            "1": {"ticker": "MSFT", "cik_str": 789019},
        }
        daily_index = """
            Description: Master Index of EDGAR Dissemination Feed
            CIK|Company Name|Form Type|Date Filed|Filename
            320193|Apple Inc.|8-K|2026-07-27|edgar/data/320193/0000320193-26-000075.txt
            320193|Apple Inc.|3|2026-07-27|edgar/data/320193/0000320193-26-000076.txt
            789019|Microsoft Corp.|10-Q|2026-07-27|edgar/data/789019/0000789019-26-000010.txt
        """
        companies = [
            {"id": 7, "symbol": "AAPL", "name": "Apple Inc."},
            {"id": 8, "symbol": "AAPL.A", "name": "Apple Inc. Class A"},
        ]
        ticker_map["2"] = {"ticker": "AAPL-A", "cik_str": 320193}

        events = fetch_sec_edgar_events(
            companies,
            now=datetime(2026, 7, 28, tzinfo=timezone.utc),
            lookback_days=1,
            user_agent="Example Owner owner@example.test",
            get_json=lambda _: ticker_map,
            get_text=lambda _: daily_index,
            pause_seconds=0,
        )

        self.assertEqual(len(events), 2)
        event = events[0]
        self.assertEqual(event.company_id, 7)
        self.assertEqual(event.article_key, "sec-edgar::0000320193-26-000075")
        self.assertEqual(event.section, "company_filing")
        self.assertIn("8-K", event.title)
        self.assertEqual(event.raw_json["cik"], "320193")
        self.assertEqual(events[1].company_id, 8)
        self.assertEqual(events[1].article_key, event.article_key)

    def test_company_ir_rss_adapter_preserves_company_link_and_release_date(self):
        feed = """
            <rss><channel><item>
              <title>Example Co. Reports Second Quarter Results</title>
              <link>https://investors.example.test/releases/q2</link>
              <guid>example-q2-2026</guid>
              <pubDate>Mon, 27 Jul 2026 08:00:00 -0400</pubDate>
              <description>Revenue increased year over year.</description>
            </item></channel></rss>
        """

        events = parse_company_ir_feed(
            {"id": 9, "symbol": "EXM", "name": "Example Co."},
            "https://investors.example.test/rss.xml",
            feed,
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.company_id, 9)
        self.assertEqual(event.source, "Company Investor Relations")
        self.assertEqual(event.published_at, "2026-07-27T12:00:00+00:00")
        self.assertEqual(event.source_url, "https://investors.example.test/releases/q2")

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
