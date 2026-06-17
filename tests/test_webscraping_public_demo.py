from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRAPY_AVAILABLE = importlib.util.find_spec("scrapy") is not None

if SCRAPY_AVAILABLE:
    from scrapy.http import HtmlResponse, Request


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
WEBSCRAPING_DIR = PYTHON_SCRIPTS_DIR / "Webscraping"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, WEBSCRAPING_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

if SCRAPY_AVAILABLE:
    from extractors.demo import extract_demo_search_links  # noqa: E402


def _html_response(url: str, html: str) -> "HtmlResponse":
    request = Request(url=url)
    return HtmlResponse(url=url, body=html.encode("utf-8"), encoding="utf-8", request=request)


@unittest.skipUnless(SCRAPY_AVAILABLE, "scrapy is not installed in this environment")
class PublicWebscrapingDemoTests(unittest.TestCase):
    def test_demo_search_link_extraction_filters_to_demo_article_paths(self):
        response = _html_response(
            "https://example.com/demo/search?q=demo",
            """
            <html>
              <body>
                <a href="/demo/articles/keep-me">Keep me</a>
                <a href="/about">Ignore me</a>
                <a href="https://example.org/demo/posts/also-keep">Also keep</a>
              </body>
            </html>
            """,
        )

        links = extract_demo_search_links(response)
        self.assertEqual(
            links,
            [
                {"href": "https://example.com/demo/articles/keep-me", "text": "Keep me"},
                {"href": "https://example.org/demo/posts/also-keep", "text": "Also keep"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
