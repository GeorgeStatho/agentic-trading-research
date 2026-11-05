"""The discovery ledger's pre-registered bar for switching DISCOVERY_MODE to enforce."""
from contextlib import closing
import json
import unittest

from db_common import get_connection

from market_db_modules.discovery_evaluation import discovery_route_ledger, promotion_check
from tests.research_foundation_fixtures import ENTRY
from tests.test_discovery_production import EnforceFixture

UNTIL = '2026-10-31T00:00:00+00:00'
FIFTEEN_DAYS_EARLIER = '2026-10-16T00:00:00+00:00'


def outcome(resolved, absolute, hit_rate):
    return {'events': resolved, 'resolved': resolved, 'mean_signed_residual_return': None,
            'hit_rate': hit_rate, 'mean_absolute_residual_return': absolute}


def check(discovery_only=None, ranking_sourced=None, first_admitted_at=FIFTEEN_DAYS_EARLIER):
    return promotion_check(discovery_only or outcome(40, 0.05, 0.55), ranking_sourced or outcome(200, 0.04, 0.5),
                           first_admitted_at=first_admitted_at, until=UNTIL)


class PromotionCheckTests(unittest.TestCase):
    def test_discovery_only_events_matching_the_ranking_on_enough_data_pass(self):
        result = check(discovery_only=outcome(40, 0.04, 0.5))
        self.assertEqual((result['passed'], result['failures']), (True, []))

    def test_each_unmet_criterion_is_named(self):
        result = check(discovery_only=outcome(39, 0.03, 0.45), first_admitted_at='2026-10-18T00:00:00+00:00')
        self.assertFalse(result['passed'])
        self.assertEqual(result['failures'], ['shadow_days_below_minimum', 'discovery_only_events_below_minimum',
                                              'mean_absolute_residual_return_below_ranking',
                                              'hit_rate_below_ranking'])

    def test_no_discovery_admission_or_unresolved_outcomes_never_pass(self):
        result = check(discovery_only=outcome(0, None, None), ranking_sourced=outcome(0, None, None),
                       first_admitted_at=None)
        self.assertEqual(result['failures'], ['shadow_days_below_minimum', 'discovery_only_events_below_minimum',
                                              'mean_absolute_residual_return_unresolved', 'hit_rate_unresolved'])

    def test_the_criteria_are_reported_with_the_verdict(self):
        self.assertEqual(check()['criteria'], {'horizon': '5d', 'min_shadow_days': 14, 'min_discovery_only_events': 40,
                                               'discovery_only_requires_company_news_days': 5,
                                               'discovery_only_at_least_ranking': ['mean_absolute_residual_return',
                                                                                   'hit_rate']})


class LedgerOriginTests(EnforceFixture):
    def production_candidate(self, candidate_id, *, routed):
        """A production candidate on the discovery event's catalyst that reached the strategist."""
        with closing(get_connection(self.db)) as conn, conn:
            queue_id, catalyst_id = conn.execute(
                "SELECT queue_id,catalyst_id FROM discovery_queue WHERE purpose='entry'").fetchone()
            conn.execute('''INSERT INTO candidates (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,
                pipeline_version_json) VALUES (?,?,?,?,?,?)''',
                         (candidate_id, 'OTHER', ENTRY, json.dumps([catalyst_id]), '[]', '{}'))
            conn.execute('INSERT INTO candidate_catalysts VALUES (?,?,?,?,?)',
                         (candidate_id, catalyst_id, 'guidance', 1, '{}'))
            conn.execute('''INSERT INTO candidate_snapshots (candidate_id,stage,captured_at,information_cutoff_time,
                pipeline_version,snapshot_version,snapshot_hash,snapshot_json) VALUES (?,?,?,?,?,?,?,?)''',
                         (candidate_id, 'pre_strategist', ENTRY, ENTRY, 'v', 'v', 'h', '{}'))
            if routed:
                conn.execute('INSERT INTO discovery_production_routes VALUES (?,?,?,?)',
                             (queue_id, 'OTHER', candidate_id, ENTRY))

    def other_event(self):
        ledger = discovery_route_ledger(days=30, as_of=ENTRY, db_path=self.db)
        event, = [row for row in ledger['admitted_events'] if row['ticker'] == 'OTHER']
        return ledger, {route: entry['status'] for route, entry in event['routes'].items()}

    def test_a_ranked_candidate_on_the_same_event_counts_as_both_routes(self):
        self.production_candidate('candidate-ranked', routed=False)
        self.assertEqual(self.other_event()[1], {'production': 'admitted', 'discovery': 'admitted'})

    def test_a_candidate_enforce_routed_to_production_still_counts_as_discovery_only(self):
        self.production_candidate('candidate-routed', routed=True)
        self.assertEqual(self.other_event()[1], {'production': 'not_evaluated', 'discovery': 'admitted'})

    def company_news(self, published_at):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO news_articles (id,source,article_key,title,source_url,published_at)
                VALUES (90,'wire','article-90','Other news','https://wire.test/90',?)''', (published_at,))
            conn.execute("INSERT INTO company_opportunist_impacts (article_id,company_id,confidence) VALUES (90,2,'high')")

    def test_a_discovery_only_event_without_company_news_does_not_count_toward_the_check(self):
        check = self.other_event()[0]['promotion_check']
        self.assertEqual((check['discovery_only']['events'], check['discovery_only_without_news']), (0, 1))

    def test_a_discovery_only_event_with_company_news_near_admission_counts(self):
        self.company_news(ENTRY)
        check = self.other_event()[0]['promotion_check']
        self.assertEqual((check['discovery_only']['events'], check['discovery_only_without_news']), (1, 0))

    def test_company_news_long_before_admission_does_not_count(self):
        self.company_news('2026-08-01T00:00:00+00:00')
        self.assertEqual(self.other_event()[0]['promotion_check']['discovery_only']['events'], 0)

if __name__ == '__main__':
    unittest.main()
