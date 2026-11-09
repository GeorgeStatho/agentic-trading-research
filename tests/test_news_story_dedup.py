"""Story dedup: syndicated copies (identical title within 12h) share one story_id.

Only identical titles merge. Live data showed body hashes collide on paywall boilerplate and
fuzzy titles merge templated headlines, so neither may group different news.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from db_common import get_connection, use_database
from market_db_modules.bootstrap import initialize_database
from news_db_modules.article_store import add_news_article
from news_db_modules.bootstrap import initialize_database as initialize_news
from news_db_modules.processing import mark_company_opportunist_article_processed
from news_db_modules.provenance import backfill_news_provenance
import agent_helpers.strategist_evidence as evidence
from agent_helpers import opportunist_support
from agent_helpers.opportunist_support import CURRENT_OPPORTUNIST_SCHEMA_VERSION, filter_unprocessed_articles

PROCESSING = "company_opportunist_article_processing"


class StoryDatabase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.db"
        initialize_database(db_path=self.db)
        initialize_news(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO sectors(id, sector_key, name) VALUES (1, 'tech', 'Technology')")
            conn.execute("INSERT INTO industries(id, sector_id, industry_key, name) VALUES (1, 1, 'software', 'Software')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (1, 1, 'PLTR', 'Palantir')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (2, 1, 'MSFT', 'Microsoft')")
        self.count = 0

    def add(self, title, published_at, body=None, source="finance.yahoo.com"):
        self.count += 1
        return add_news_article(
            source=source, article_key=f"key-{self.count}", title=title,
            source_url=f"https://{source}/story/{self.count}", body=body or f"Body of {title}",
            published_at=published_at, db_path=self.db,
        )

    def story(self, article_id):
        with closing(get_connection(self.db)) as conn:
            return conn.execute("SELECT story_id FROM news_articles WHERE id = ?", (article_id,)).fetchone()[0]


class StoryAssignmentTests(StoryDatabase):
    def test_syndicated_copy_within_hours_joins_the_first_story(self):
        first = self.add("1 Reason to Buy AMD Stock Before 2027", "2026-11-05T14:00:00+00:00", source="fool.com")
        repost = self.add("1 Reason to Buy AMD Stock Before 2027", "2026-11-05T15:00:00+00:00")
        self.assertEqual(self.story(first), first)
        self.assertEqual(self.story(repost), first)

    def test_recurring_daily_column_stays_separate(self):
        monday = self.add("Sector Update: Consumer Stocks Gain Late Afternoon", "2026-11-02T20:00:00+00:00")
        tuesday = self.add("Sector Update: Consumer Stocks Gain Late Afternoon", "2026-11-03T20:00:00+00:00")
        self.assertNotEqual(self.story(monday), self.story(tuesday))

    def test_shared_boilerplate_body_does_not_merge_different_news(self):
        paywall = "Subscribe to continue reading this article."
        ubs = self.add("UBS Adjusts Chevron Price Target to $265 From $235", "2026-11-04T12:00:00+00:00", paywall)
        hsbc = self.add("HSBC Adjusts Chevron Price Target to $263 From $250", "2026-11-04T12:30:00+00:00", paywall)
        self.assertNotEqual(self.story(ubs), self.story(hsbc))

    def test_same_source_generic_title_with_different_bodies_stays_separate(self):
        # Live audit: four different bond notices and two different sector updates shared one title.
        notices = [self.add("FRN Variable Rate Fix", "2026-11-05T10:00:00+00:00", body=f"Notice for bond {n}")
                   for n in range(2)]
        morning = self.add("Sector Update: Energy", "2026-11-05T13:00:00+00:00", body="Energy stocks rise premarket")
        afternoon = self.add("Sector Update: Energy", "2026-11-05T17:00:00+00:00", body="Energy stocks fall late")
        self.assertNotEqual(self.story(notices[0]), self.story(notices[1]))
        self.assertNotEqual(self.story(morning), self.story(afternoon))

    def test_same_source_repost_with_the_same_body_joins_the_story(self):
        body = "Daiwa Securities upgraded Fortinet to outperform."
        first = self.add("Daiwa Securities Upgrades Fortinet to Outperform", "2026-11-05T10:00:00+00:00", body=body)
        repost = self.add("Daiwa Securities Upgrades Fortinet to Outperform", "2026-11-05T12:00:00+00:00", body=body)
        self.assertEqual(self.story(repost), first)

    def test_templated_headlines_about_different_companies_stay_separate(self):
        revvity = self.add("Revvity, Inc. Q3 2026 Earnings Call Summary", "2026-11-04T12:00:00+00:00")
        ww = self.add("WW International, Inc. Q3 2026 Earnings Call Summary", "2026-11-04T12:10:00+00:00")
        self.assertNotEqual(self.story(revvity), self.story(ww))

    def test_every_copy_points_at_the_first_article_not_a_chain(self):
        title = "Uber Bookings Jump 20% as Profit Grows Faster"
        ids = [self.add(title, f"2026-11-05T1{hour}:00:00+00:00") for hour in (0, 1, 2)]
        self.assertEqual([self.story(article_id) for article_id in ids], [ids[0]] * 3)

    def test_backfill_fills_a_missing_story_id(self):
        first = self.add("Why Nvidia Stock Looks Like a Bargain Right Now", "2026-11-05T10:00:00+00:00")
        repost = self.add("Why Nvidia Stock Looks Like a Bargain Right Now", "2026-11-05T11:00:00+00:00")
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE news_articles SET story_id = NULL")
        backfill_news_provenance(db_path=self.db)
        self.assertEqual((self.story(first), self.story(repost)), (first, first))


class OpportunistSkipTests(StoryDatabase):
    def setUp(self):
        super().setUp()
        self.marked = []

    def mark(self, article_id, record):
        self.marked.append((article_id, record["duplicate_of_article_id"]))

    def processed(self, article_id, company_id=1):
        mark_company_opportunist_article_processed(
            article_id=article_id, company_id=company_id, model="m", db_path=self.db,
            raw_json={"schema_version": CURRENT_OPPORTUNIST_SCHEMA_VERSION, "impacts": []},
        )

    def drop(self, article_ids, company_id=1, mark=None):
        with use_database(self.db):
            kept = opportunist_support.drop_story_duplicates([{"article_id": article_id} for article_id in article_ids],
                                         table_name=PROCESSING, company_id=company_id, mark=mark or self.mark)
        return [article["article_id"] for article in kept]

    def test_copy_of_an_already_analyzed_story_is_marked_and_skipped(self):
        first = self.add("Palantir Grows Revenue 96%", "2026-11-05T10:00:00+00:00")
        repost = self.add("Palantir Grows Revenue 96%", "2026-11-05T11:00:00+00:00")
        self.processed(first)

        def mark(article_id, record):
            self.mark(article_id, record)
            mark_company_opportunist_article_processed(article_id=article_id, company_id=1, model="story-duplicate",
                                                       raw_json=record, db_path=self.db)

        self.assertEqual(self.drop([repost], mark=mark), [])
        self.assertEqual(self.marked, [(repost, first)])
        remaining = filter_unprocessed_articles([{"article_id": repost}], db_path=str(self.db), table_name=PROCESSING,
                                                company_id=1)
        self.assertEqual(remaining, [])

    def test_story_analyzed_only_for_another_company_is_kept(self):
        first = self.add("Palantir Grows Revenue 96%", "2026-11-05T10:00:00+00:00")
        repost = self.add("Palantir Grows Revenue 96%", "2026-11-05T11:00:00+00:00")
        self.processed(first, company_id=2)
        self.assertEqual(self.drop([repost], company_id=1), [repost])
        self.assertEqual(self.marked, [])

    def test_second_unanalyzed_copy_waits_for_the_first_without_being_marked(self):
        first = self.add("Sandisk Shares Sink Despite Surging Sales", "2026-11-05T10:00:00+00:00")
        repost = self.add("Sandisk Shares Sink Despite Surging Sales", "2026-11-05T11:00:00+00:00")
        self.assertEqual(self.drop([repost, first]), [repost])
        self.assertEqual(self.marked, [])

    def test_distinct_stories_are_all_kept(self):
        ids = [self.add(f"Distinct headline {n}", "2026-11-05T10:00:00+00:00") for n in range(3)]
        self.assertEqual(self.drop(ids), ids)
        self.assertEqual(self.marked, [])

    def test_company_batch_builder_skips_copies_of_analyzed_stories(self):
        import agent_helpers.company_opportunist as company_opportunist
        first = self.add("Palantir Grows Revenue 96%", "2026-11-05T10:00:00+00:00")
        repost = self.add("Palantir Grows Revenue 96%", "2026-11-05T11:00:00+00:00")
        other = self.add("Palantir signs Army deal", "2026-11-05T12:00:00+00:00")
        self.processed(first)
        raw = [{"id": article_id, "title": "", "published_at": "2026-11-05T12:00:00+00:00"}
               for article_id in (first, repost, other)]
        with use_database(self.db), patch.object(company_opportunist, "get_company_reference",
                                                 return_value=({"company_id": 1}, {}, raw)):
            _, _, articles = company_opportunist.build_company_opportunist_articles("PLTR")
        self.assertEqual([article["article_id"] for article in articles], [other])
        with closing(get_connection(self.db)) as conn:
            model = conn.execute(f"SELECT model FROM {PROCESSING} WHERE article_id = ?", (repost,)).fetchone()[0]
        self.assertEqual(model, "story-duplicate")


class StrategistEvidenceDedupTests(StoryDatabase):
    def sections(self, **lists):
        names = {
            "company_impacts": "get_high_confidence_company_news",
            "industry_impacts": "get_high_confidence_industry_news",
            "sector_impacts": "get_high_confidence_sector_news",
            "sector_rss_articles": "get_sector_news_for_company_sector",
            "macro_impacts": "get_macro_news_for_company_sector",
        }
        patches = [patch.object(evidence, "get_company_context", return_value=({}, {}))]
        patches += [patch.object(evidence, getter, return_value=[{"article_id": article_id} for article_id in
                                                                  lists.get(key, [])]) for key, getter in names.items()]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        with use_database(self.db):
            result = evidence.build_strategist_evidence_sections("X")
        return {key: [item["article_id"] for item in result[key]] for key in names}

    def test_article_in_sector_and_macro_views_is_kept_once_in_the_sector_view(self):
        shared = self.add("Qualcomm stock drops 5% on weak forecast", "2026-11-05T10:00:00+00:00")
        macro_only = self.add("Fed holds rates steady", "2026-11-05T10:00:00+00:00")
        result = self.sections(sector_impacts=[shared], macro_impacts=[shared, macro_only])
        self.assertEqual((result["sector_impacts"], result["macro_impacts"]), ([shared], [macro_only]))

    def test_repost_pair_keeps_the_first_listed_newest_copy(self):
        first = self.add("Microsoft Soars 7% to Record", "2026-11-05T10:00:00+00:00")
        repost = self.add("Microsoft Soars 7% to Record", "2026-11-05T11:00:00+00:00")
        self.assertEqual(self.sections(company_impacts=[repost, first])["company_impacts"], [repost])

    def test_distinct_stories_are_all_kept(self):
        ids = [self.add(f"Distinct headline {n}", "2026-11-05T10:00:00+00:00") for n in range(3)]
        self.assertEqual(self.sections(company_impacts=ids)["company_impacts"], ids)


if __name__ == "__main__":
    unittest.main()
