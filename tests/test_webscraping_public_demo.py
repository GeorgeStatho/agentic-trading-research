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
    from engine.article_extraction import extract_from_response  # noqa: E402
    from extractors.demo import extract_demo_search_links  # noqa: E402
    from pipelines.job_builder import build_company_source_jobs  # noqa: E402


def _html_response(url: str, html: str) -> "HtmlResponse":
    request = Request(url=url)
    return HtmlResponse(url=url, body=html.encode("utf-8"), encoding="utf-8", request=request)


@unittest.skipUnless(SCRAPY_AVAILABLE, "scrapy is not installed in this environment")
class PublicWebscrapingDemoTests(unittest.TestCase):
    def test_generic_article_extraction_uses_mocked_html(self):
        response = _html_response(
            "https://example.com/demo/articles/demo-story",
            """
            <html>
              <head>
                <title>Fallback Title</title>
                <meta property="article:published_time" content="2026-05-20T12:00:00Z" />
              </head>
              <body>
                <article>
                  <h1>Demo Story</h1>
                  <p>First paragraph.</p>
                  <p>Second paragraph.</p>
                </article>
              </body>
            </html>
            """,
        )

        result = extract_from_response(response)
        self.assertTrue(result.success)
        self.assertEqual(result.title, "Demo Story")
        self.assertIn("First paragraph.", result.text)
        self.assertIn("Second paragraph.", result.text)
        self.assertEqual(result.published_at, "2026-05-20T12:00:00+00:00")

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

    def test_job_builder_uses_public_demo_sources(self):
        jobs = build_company_source_jobs(
            [
                {
                    "name": "Example Corp",
                    "symbol": "DEMO",
                    "raw_json": {"exchange": "NASDAQ"},
                }
            ]
        )

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["search_term"], "Example Corp")
        self.assertIn("example.com/demo/search", jobs[0]["url"])
        self.assertIn("example.com/demo/companies/demo", jobs[1]["url"])


if __name__ == "__main__":
    unittest.main()
