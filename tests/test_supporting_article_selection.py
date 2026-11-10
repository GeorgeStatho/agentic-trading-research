from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_builders.strategist_payload_articles import _build_supporting_articles

SYMBOL = "AAPL"
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
# Distinct id ranges per bucket make the expected order readable in assertions.
COMPANY, INDUSTRY, SECTOR, MACRO, RSS = 100, 200, 300, 400, 500


def item(article_id, *, confidence="high", primary_ticker=None, body="", minutes=None):
    """One evidence row, shaped like what _upsert_supporting_article reads."""
    published = BASE + timedelta(minutes=article_id if minutes is None else minutes)
    return {
        "article_id": article_id,
        "title": f"title {article_id}",
        "summary": f"summary {article_id}",
        "body": body,
        "source": "wire",
        "source_url": f"https://example.com/{article_id}",
        "published_at": published.isoformat(),
        "article_scope": "",
        "confidence": confidence,
        "reason": "r",
        "evidence_score": 1.0,
        "primary_ticker": primary_ticker,
    }


def evidence(*, company=(), industry=(), sector=(), macro=(), rss=(), symbol=SYMBOL):
    return {
        "company": {"symbol": symbol},
        "company_impacts": list(company),
        "industry_impacts": list(industry),
        "sector_impacts": list(sector),
        "macro_impacts": list(macro),
        "sector_rss_articles": list(rss),
    }


def build(ev, *, summary_limit=20, full_limit=5):
    return _build_supporting_articles(ev, summary_article_limit=summary_limit, full_article_limit=full_limit)


def summary_ids(result):
    return [a["article_id"] for a in result["article_summaries"]]


def ids(start, count):
    return list(range(start, start + count))


class CompanyFirstTests(unittest.TestCase):
    def test_company_articles_outrank_higher_scoring_macro_articles(self):
        macro_ids = ids(MACRO, 15)
        company_ids = ids(COMPANY, 3)
        ev = evidence(
            macro=[item(i) for i in macro_ids],
            sector=[item(i) for i in macro_ids],  # same articles again: multi-layer, high confidence
            company=[item(i, confidence="medium") for i in company_ids],
        )
        got = summary_ids(build(ev))
        self.assertEqual(sorted(got[:3]), company_ids)
        self.assertEqual(len(got), 18)

    def test_primary_ticker_match_arriving_only_via_macro_is_company(self):
        ticker_article = MACRO + 99
        macro_only = ids(MACRO, 5)
        ev = evidence(
            macro=[item(i) for i in macro_only]
            # lowercase on purpose: the symbol comparison is case-insensitive
            + [item(ticker_article, confidence="medium", primary_ticker="aapl", minutes=0)],
            sector=[item(i) for i in macro_only],
        )
        self.assertEqual(summary_ids(build(ev))[0], ticker_article)

    def test_other_ticker_in_macro_is_not_company(self):
        other = MACRO + 99
        ev = evidence(
            macro=[item(i) for i in ids(MACRO, 3)] + [item(other, confidence="medium", primary_ticker="MSFT", minutes=0)],
            company=[item(COMPANY, confidence="medium")],
        )
        got = summary_ids(build(ev))
        self.assertEqual(got[0], COMPANY)
        self.assertEqual(got[-1], other)


class QuotaTests(unittest.TestCase):
    def test_quotas_ten_five_three_two_in_bucket_order(self):
        company, industry = ids(COMPANY, 12), ids(INDUSTRY, 7)
        sector, macro = ids(SECTOR, 5), ids(MACRO, 5)
        ev = evidence(
            company=[item(i) for i in company],
            industry=[item(i) for i in industry],
            sector=[item(i) for i in sector],
            macro=[item(i) for i in macro],
        )
        # Equal confidence, so within a bucket the more recent (higher id) article comes first.
        newest = lambda pool, n: sorted(pool, reverse=True)[:n]
        expected = newest(company, 10) + newest(industry, 5) + newest(sector, 3) + newest(macro, 2)
        self.assertEqual(summary_ids(build(ev)), expected)

    def test_unfilled_quotas_are_backfilled_from_the_largest_pool(self):
        company, industry, macro = ids(COMPANY, 2), ids(INDUSTRY, 1), ids(MACRO, 30)
        ev = evidence(
            company=[item(i) for i in company],
            industry=[item(i) for i in industry],
            macro=[item(i) for i in macro],
        )
        got = summary_ids(build(ev))
        # 2 macro by quota + 15 by backfill, all in priority (newest-first) order.
        expected = sorted(company, reverse=True) + industry + sorted(macro, reverse=True)[:17]
        self.assertEqual(got, expected)
        self.assertEqual(len(got), 20)

    def test_limit_ten_scales_quotas_to_five_two_one_one_then_backfills(self):
        company, industry = ids(COMPANY, 8), ids(INDUSTRY, 4)
        sector, macro = ids(SECTOR, 3), ids(MACRO, 3)
        ev = evidence(
            company=[item(i) for i in company],
            industry=[item(i) for i in industry],
            sector=[item(i) for i in sector],
            macro=[item(i) for i in macro],
        )
        by_recency = lambda pool: sorted(pool, reverse=True)
        expected = (
            by_recency(company)[:5] + by_recency(industry)[:2] + by_recency(sector)[:1] + by_recency(macro)[:1]
            + by_recency(company)[5:6]  # the one backfill slot goes to the first leftover bucket
        )
        got = summary_ids(build(ev, summary_limit=10))
        self.assertEqual(got, expected)
        self.assertEqual(len(got), 10)

    def test_summaries_never_exceed_the_limit(self):
        ev = evidence(company=[item(i) for i in ids(COMPANY, 30)])
        self.assertEqual(len(build(ev, summary_limit=7)["article_summaries"]), 7)


class UnsignalledTests(unittest.TestCase):
    def test_sector_rss_only_articles_come_after_every_signalled_article(self):
        company, macro, rss = ids(COMPANY, 3), ids(MACRO, 2), ids(RSS, 4)
        ev = evidence(
            company=[item(i, confidence="low") for i in company],
            macro=[item(i, confidence="low") for i in macro],
            # newer than everything else, so only the bucket rule can put them last
            rss=[item(i, minutes=100_000 + i) for i in rss],
        )
        got = summary_ids(build(ev))
        self.assertEqual(set(got[:5]), set(company + macro))
        self.assertEqual(set(got[5:]), set(rss))

    def test_sector_rss_only_articles_do_not_displace_signalled_ones(self):
        company, rss = ids(COMPANY, 5), ids(RSS, 4)
        ev = evidence(company=[item(i) for i in company], rss=[item(i, minutes=100_000 + i) for i in rss])
        self.assertEqual(set(summary_ids(build(ev, summary_limit=5))), set(company))


class WithinBucketOrderTests(unittest.TestCase):
    def test_high_confidence_before_medium_then_most_recent_first(self):
        ev = evidence(macro=[
            item(401, confidence="medium", minutes=500),  # newest, but medium
            item(402, confidence="high", minutes=100),
            item(403, confidence="high", minutes=300),
            item(404, confidence="medium", minutes=400),
        ])
        self.assertEqual(summary_ids(build(ev)), [403, 402, 401, 404])


class FullArticleTests(unittest.TestCase):
    def test_company_article_with_body_precedes_higher_scoring_macro_article(self):
        ev = evidence(
            macro=[item(MACRO, body="macro body")],
            sector=[item(MACRO, body="macro body")],
            company=[
                item(COMPANY, confidence="medium", body="company body"),
                item(COMPANY + 1, confidence="high"),  # no body: must not appear
            ],
            industry=[item(INDUSTRY, body="industry body")],
        )
        got = [a["article_id"] for a in build(ev)["full_articles"]]
        self.assertEqual(got, [COMPANY, INDUSTRY, MACRO])

    def test_full_article_limit_is_applied_after_preference(self):
        ev = evidence(
            macro=[item(i, body="b") for i in ids(MACRO, 5)],
            company=[item(i, body="b") for i in ids(COMPANY, 2)],
        )
        got = [a["article_id"] for a in build(ev, full_limit=3)["full_articles"]]
        self.assertEqual(sorted(got[:2]), ids(COMPANY, 2))
        self.assertEqual(len(got), 3)


class ShapeTests(unittest.TestCase):
    def test_article_fields_are_unchanged(self):
        ev = evidence(company=[item(COMPANY, body="body")])
        result = build(ev)
        summary_keys = {
            "article_id", "title", "summary", "source", "source_url", "published_at", "article_scope",
            "evidence_score", "root_event_id", "root_source_id", "primary_ticker", "catalyst_id",
            "source_type", "independent_source_count", "evidence_layers", "agent_signals",
        }
        self.assertEqual(set(result["article_summaries"][0]), summary_keys)
        self.assertEqual(set(result["full_articles"][0]), summary_keys | {"body"})


class ManifestTests(unittest.TestCase):
    def test_manifest_records_the_selection_version(self):
        from services.core.pipeline_version import build_pipeline_version

        manifest = build_pipeline_version()
        self.assertEqual(manifest["supporting_article_selection_version"], "company-first-quotas-v2")


if __name__ == "__main__":
    unittest.main()


class SummaryTextTests(unittest.TestCase):
    # Yahoo and Benzinga search links store no summary and Fool stores the headline; the model then saw only titles.
    def summary(self, **fields):
        row = {**item(COMPANY, primary_ticker=SYMBOL), **fields}
        return build(evidence(company=[row]))["article_summaries"][0]["summary"]

    def test_empty_summary_becomes_the_body_lead(self):
        body = "Sandisk fell 9% as investors questioned  its margins.\n" + "More detail follows here. " * 30
        for summary in ("", None, "   "):
            with self.subTest(summary=summary):
                lead = self.summary(summary=summary, body=body)
                self.assertTrue(lead.startswith("Sandisk fell 9% as investors questioned its margins. More"))
                self.assertLessEqual(len(lead), 300)
                self.assertIn(lead.rsplit(" ", 1)[0], " ".join(body.split()))  # a prefix of the body, cut between words
                self.assertFalse(lead.endswith(" "))

    def test_real_summary_and_short_or_missing_bodies(self):
        self.assertEqual(self.summary(summary="Publisher summary", body="Long body " * 50), "Publisher summary")
        # Fool repeats the headline as its summary; its body opens with navigation, so the headline stays.
        self.assertEqual(self.summary(summary=f"title {COMPANY}", body="Our Services All Services " * 30),
                         f"title {COMPANY}")
        self.assertEqual(self.summary(summary="", body="Short body."), "Short body.")
        self.assertEqual(self.summary(summary="", body=""), "")


class FullArticleRankingTests(unittest.TestCase):
    # 2026-11-09, ANET: every article had one high-confidence signal, so recency alone picked full text, and
    # an indirect, minimal-impact Nokia piece beat "Arista Networks Posts Fastest Growth Since IPO".
    def test_direct_high_materiality_beats_a_newer_indirect_article(self):
        direct = {**item(COMPANY, body="Arista posts fastest growth.", minutes=1),
                  "effect_type": "direct", "materiality": "high"}
        indirect = {**item(COMPANY + 1, body="Nokia stock rises.", minutes=50),
                    "effect_type": "indirect", "materiality": "medium"}
        result = build(evidence(company=[indirect, direct]), full_limit=1)
        self.assertEqual([a["article_id"] for a in result["full_articles"]], [COMPANY])


class BoilerplateTests(unittest.TestCase):
    DISCLAIMER = ("The above button links to Coinbase. Yahoo Finance is not a broker-dealer or investment adviser and "
                  "does not offer securities or cryptocurrencies for sale or facilitate trading. Coinbase pays us for "
                  "certain activity generated through this link. Prices displayed are informational.\n")

    def test_the_coinbase_disclaimer_is_not_part_of_the_article(self):
        row = {**item(COMPANY, body=self.DISCLAIMER + "(Updates with index moves) Stocks rose."), "summary": ""}
        result = build(evidence(company=[row]))
        self.assertEqual(result["full_articles"][0]["body"], "(Updates with index moves) Stocks rose.")
        self.assertEqual(result["article_summaries"][0]["summary"], "(Updates with index moves) Stocks rose.")

    def test_other_bodies_are_unchanged(self):
        body = "  Coinbase shares rose. The above button links to Coinbase.  "
        self.assertEqual(build(evidence(company=[item(COMPANY, body=body)]))["full_articles"][0]["body"], body)
