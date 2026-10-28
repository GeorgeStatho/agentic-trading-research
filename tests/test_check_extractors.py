import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.article_extraction import ArticleExtractionResult
from engine.page_fetch import FixtureFetcher, use_fetcher
from scripts import check_extractors as check

DOMAIN = "finance.yahoo.com"
URLS = [f"https://finance.yahoo.com/news/story-{n}.html" for n in range(10)]


def _article(url, good=True):
    return ArticleExtractionResult(
        url=url,
        title="Title" if good else "",
        text="word " * 200 if good else "",
        published_at="2026-10-27T10:00:00Z" if good else "",
        success=good,
    )


class CheckDomainTests(unittest.TestCase):
    def run_check(self, urls, good_count, loads_used=0, limit=10):
        fetcher = FixtureFetcher(articles={u: _article(u, i < good_count) for i, u in enumerate(urls)})
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                limit=limit, ledger=Path(tmp) / "loads.json", max_loads=15, reuse_urls=False, listing_url=None
            )
            ledger = {DOMAIN: {"loads": loads_used, "urls": []}}
            with mock.patch.object(check, "yahoo_urls", return_value=urls), use_fetcher(fetcher), \
                    contextlib.redirect_stdout(io.StringIO()):
                summary = check.check_domain(DOMAIN, args, ledger)
        return summary, ledger, fetcher

    def test_passes_at_seven_of_ten(self):
        summary, ledger, _ = self.run_check(URLS, 7)

        self.assertTrue(summary["passed"])
        self.assertEqual((summary["ok"], summary["total"]), (7, 10))
        self.assertEqual(ledger[DOMAIN]["loads"], 10)

    def test_fails_at_six_of_ten(self):
        summary, _, _ = self.run_check(URLS, 6)

        self.assertFalse(summary["passed"])

    def test_a_short_body_or_unparseable_date_is_not_ok(self):
        short = ArticleExtractionResult(URLS[0], "T", "short", "2026-10-27T10:00:00Z", True)
        undated = ArticleExtractionResult(URLS[1], "T", "word " * 200, "yesterday", True)

        self.assertFalse(check.check_row(short)[0])
        self.assertFalse(check.check_row(undated)[0])

    def test_never_fetches_past_the_load_budget(self):
        summary, ledger, fetcher = self.run_check(URLS, 10, loads_used=12)

        self.assertEqual(len(fetcher.requested), 3)
        self.assertEqual(ledger[DOMAIN]["loads"], 15)

    def test_no_article_links_is_its_own_failure(self):
        summary, _, fetcher = self.run_check([], 0)

        self.assertFalse(summary["passed"])
        self.assertTrue(summary["error"].startswith("NO_ARTICLE_LINKS"))
        self.assertEqual(fetcher.requested, [])


if __name__ == "__main__":
    unittest.main()
