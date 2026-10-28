from __future__ import annotations

import json
import unittest
from pathlib import Path

from scrapy.http import HtmlResponse

from engine.article_extraction import _normalize_datetime, extract_from_response
from sources.source_config import CONFIG_PATH, get_article_patterns

FIXTURES = Path(__file__).parent / "fixtures" / "extractors"


def _extract(name: str, url: str):
    body = (FIXTURES / name).read_bytes()
    return extract_from_response(HtmlResponse(url=url, body=body, encoding="utf-8"))


class YahooExtractorFixtureTests(unittest.TestCase):
    # Pages saved from live finance.yahoo.com article URLs on 2026-10-27, trimmed to the head metadata and <article>.

    def test_syndicated_motley_fool_article_has_clean_body(self):
        result = _extract(
            "yahoo_motley_fool_syndication.html",
            "https://finance.yahoo.com/markets/stocks/articles/why-sofi-stock-popped-today-023013531.html",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.title, "Why SoFi Stock Popped Today")
        self.assertTrue(_normalize_datetime(result.published_at))
        self.assertGreaterEqual(len(result.text), 500)
        # Link text stays inside its sentence instead of being dropped.
        self.assertIn("Shares of SoFi Technologies (NASDAQ:SOFI) rose", " ".join(result.text.split()))
        # The broker-dealer tooltip is page chrome, not the article.
        self.assertNotIn("Coinbase", result.text)

    def test_wall_st_article_has_clean_body(self):
        result = _extract(
            "yahoo_simply_wall_st.html",
            "https://finance.yahoo.com/technology/ai/articles/does-super-micro-computer-smci-011331935.html",
        )

        self.assertTrue(result.success)
        self.assertTrue(result.title.startswith("Does Super Micro Computer (SMCI)"))
        self.assertTrue(_normalize_datetime(result.published_at))
        self.assertGreaterEqual(len(result.text), 500)
        self.assertNotIn("Coinbase", result.text)


class ExtractorSourceConfigTests(unittest.TestCase):
    # Live check on 2026-10-27 (scripts/check_extractors.py): only Yahoo passes from this environment.
    # MarketWatch 401s Scrapy (bot wall) and Investing 403s Scrapy and requests, so no article URLs could be listed;
    # Morningstar fetched 6/10 (4 hit an AWS WAF challenge that only a JS browser answers). Playwright's browser
    # is not installed here, so none of the three could be repaired. They stay off until re-checked.
    EXPECTED_ALLOWED = {
        "finance.yahoo.com": True,
        "marketwatch.com": False,
        "morningstar.com": False,
        "investing.com": False,
    }

    def test_allowed_set_of_checked_domains(self):
        domains = json.loads(Path(CONFIG_PATH).read_text(encoding="utf-8"))["domains"]

        self.assertEqual({d: domains[d]["allowed"] for d in self.EXPECTED_ALLOWED}, self.EXPECTED_ALLOWED)

    def test_yahoo_patterns_cover_the_current_article_url_shapes(self):
        # yf.Search returns /markets/stocks/articles/... and /technology/ai/articles/...; "/news/" alone skipped them all.
        for url in (
            "https://finance.yahoo.com/markets/stocks/articles/why-sofi-stock-popped-today-023013531.html",
            "https://finance.yahoo.com/technology/ai/articles/does-super-micro-computer-smci-011331935.html",
            "https://finance.yahoo.com/news/some-story-123.html",
        ):
            self.assertTrue(any(p in url for p in get_article_patterns(url)), url)


if __name__ == "__main__":
    unittest.main()
