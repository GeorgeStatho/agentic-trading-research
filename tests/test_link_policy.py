from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from news_db_modules.article_store import add_news_article
from news_db_modules.queries import add_failed_url, get_saved_and_failed_urls
from pipelines._link_policy import LinkBatch, judge_links
from tests.db_fixtures import temporary_database


def _url(name: str, days_old: int = 0) -> str:
    day = datetime.now(timezone.utc) - timedelta(days=days_old)
    return f"https://www.cnbc.com/{day:%Y/%m/%d}/{name}.html"


def _link(href: str | None, text: str = "t") -> dict:
    return {"href": href, "text": text}


def _seed(url: str, days_old: int = 0) -> int:
    published_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return add_news_article(source="cnbc.com", article_key=url, title=url, source_url=url, published_at=published_at)


class LinkPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)

    def _judge(self, links, max_articles=10, may_reuse=None, **kwargs):
        return judge_links([LinkBatch(links, max_articles, may_reuse)], **kwargs)[0]

    def _one(self, link, **kwargs):
        (verdict,) = self._judge([link], **kwargs)
        return verdict

    def test_each_skip_reason(self):
        cases = {
            "no_href": (_link(None), {}),
            "excluded": (_link(_url("a")), {"should_include_link": lambda href, link: False}),
            "disallowed_source": (_link("https://example.com/2026/01/01/x.html"), {}),
            "not_article_url": (_link("https://www.cnbc.com/quotes/AAPL"), {}),
            "blacklisted": (_link(_url("b").replace("cnbc.com/", "cnbc.com/pro/news/")), {}),
            "stale_url_date": (_link(_url("c", days_old=60)), {}),
        }
        for reason, (link, kwargs) in cases.items():
            with self.subTest(reason=reason):
                verdict = self._one(link, **kwargs)
                self.assertEqual((verdict.action, verdict.reason), ("skip", reason))

    def test_fool_api_article_paths_pass_the_pattern_check(self):
        for prefix in ("investing", "market", "retirement", "money", "personal-finance"):
            with self.subTest(prefix=prefix):
                verdict = self._one(_link(f"https://www.fool.com/{prefix}/2026/01/02/some-story/"))
                self.assertEqual(verdict.action, "fetch")

    def test_duplicate_within_batch_including_utm_variant(self):
        url = _url("dup")
        verdicts = self._judge([_link(url), _link(url + "?utm_source=x"), _link(url)])
        self.assertEqual([v.action for v in verdicts], ["fetch", "skip", "skip"])
        self.assertEqual({v.reason for v in verdicts[1:]}, {"duplicate"})

    def test_order_href_and_max_age_are_kept(self):
        urls = [_url("one"), _url("two")]
        verdicts = self._judge([_link(f" {urls[0]} "), _link(""), _link(urls[1])])
        self.assertEqual([v.href for v in verdicts], [urls[0], "", urls[1]])
        self.assertEqual([v.link["href"] for v in verdicts], [f" {urls[0]} ", "", urls[1]])
        self.assertEqual(verdicts[0].max_age_days, 14)
        self.assertTrue(verdicts[0].normalized_url.startswith("https://www.cnbc.com/"))

    def test_existing_recent_is_reused_and_stale_is_skipped(self):
        recent, old = _url("recent"), _url("older", days_old=5)
        recent_id = _seed(recent)
        _seed(old, days_old=30)
        reused, stale = self._judge([_link(recent), _link(old)])
        self.assertEqual((reused.action, reused.article_id), ("reuse", recent_id))
        self.assertEqual((stale.action, stale.reason, stale.article_id), ("skip", "stale_existing", None))

    def test_may_reuse_false_skips_as_irrelevant_but_does_not_affect_new_links(self):
        existing, new = _url("existing"), _url("new")
        _seed(existing)
        reuse_verdict, fetch_verdict = self._judge([_link(existing), _link(new)], may_reuse=lambda link: False)
        self.assertEqual((reuse_verdict.action, reuse_verdict.reason), ("skip", "irrelevant_existing"))
        self.assertEqual(fetch_verdict.action, "fetch")

    def test_failed_url_threshold_and_permanent(self):
        two, three, permanent = _url("two"), _url("three"), _url("perm")
        for _ in range(2):
            add_failed_url(two)
        for _ in range(3):
            add_failed_url(three)
        add_failed_url(permanent, is_permanent=True)
        verdicts = self._judge([_link(two), _link(three), _link(permanent)])
        self.assertEqual(
            [(v.action, v.reason) for v in verdicts],
            [("fetch", ""), ("skip", "failed_url"), ("skip", "failed_url")],
        )

    def test_limit_counts_fetches_only(self):
        existing = _url("existing")
        _seed(existing)
        verdicts = self._judge([_link(_url(n)) for n in ("a", "b", "c")] + [_link(existing)], max_articles=2)
        self.assertEqual(
            [(v.action, v.reason) for v in verdicts],
            [("fetch", ""), ("fetch", ""), ("skip", "limit"), ("reuse", "")],
        )

    def test_same_url_in_two_batches_is_fetch_in_both(self):
        url = _url("shared")
        first, second = judge_links([LinkBatch([_link(url)], 10), LinkBatch([_link(url)], 10)])
        self.assertEqual((first[0].action, second[0].action), ("fetch", "fetch"))

    def test_single_lookup_for_survivors_and_none_without_any(self):
        with patch("pipelines._link_policy.get_saved_and_failed_urls", wraps=get_saved_and_failed_urls) as lookup:
            judge_links([LinkBatch([_link(_url("a"))], 10), LinkBatch([_link(_url("b")), _link(_url("a"))], 10)])
            self.assertEqual(lookup.call_count, 1)
            self.assertEqual(len(lookup.call_args.args[0]), 2)

            lookup.reset_mock()
            judge_links([LinkBatch([_link(None), _link("https://example.com/x")], 10)])
            lookup.assert_not_called()

    def test_lookup_handles_empty_input_and_more_than_one_chunk(self):
        self.assertEqual(get_saved_and_failed_urls([]), ({}, {}))

        seeded = _url("seeded")
        article_id = _seed(seeded)
        add_failed_url("https://www.cnbc.com/failed")
        urls = [f"https://www.cnbc.com/other/{i}" for i in range(1200)]
        urls.insert(700, "https://www.cnbc.com/failed")
        urls.append(seeded.rstrip("/"))
        saved, failed = get_saved_and_failed_urls(urls)

        self.assertEqual(list(saved), [seeded])
        self.assertEqual(saved[seeded]["id"], article_id)
        self.assertEqual(failed, {"https://www.cnbc.com/failed": {"failure_count": 1, "is_permanent": 0}})


if __name__ == "__main__":
    unittest.main()
