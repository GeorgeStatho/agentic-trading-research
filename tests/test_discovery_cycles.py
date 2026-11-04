"""Discovery admission stays in the trading cycle; model-backed research runs in the collector."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from db_common import get_connection
from market_db_modules.discovery_queue import enqueue_discovery_event
from news_db_modules.catalysts import build_catalyst_state, store_catalyst_state
from research_foundation_contracts import DiscoveryQueueItem
from services.discovery.discovery_policy import DiscoveryPolicy
from services.discovery import discovery_queue
from services.research.evidence_research_policy import EvidenceResearchPolicy
from services.research.research_orchestrator import ResearchOrchestrator
from tests.research_foundation_fixtures import START, ENTRY
from tests.test_event_discovery import DiscoveryFixture, later
from tests.test_research_orchestrator import FakePlanner


def no_model_work(*args, **kwargs):
    raise AssertionError('The trading cycle must not run research or theses')


class TradingCycleTests(DiscoveryFixture):
    def test_the_worker_cycle_admits_events_without_research_or_theses(self):
        self.package()
        with patch.object(discovery_queue, 'process_research_queue', side_effect=no_model_work), \
                patch.object(discovery_queue, 'process_research_theses', side_effect=no_model_work):
            result = discovery_queue.run_discovery_shadow_cycle(policy=self.policy, clock=lambda: ENTRY, db_path=self.db)
        self.assertEqual([c['symbol'] for c in result['candidates']], ['NVDA'])
        self.assertNotIn('research', result)

    def enqueue_four(self):
        with closing(get_connection(self.db)) as conn, conn:
            for company_id, symbol in ((1, 'NVDA'), (2, 'OTHER')):
                for index in range(2):
                    catalyst_id = f'catalyst:batch-{symbol}-{index}'
                    store_catalyst_state(build_catalyst_state(catalyst_id, [], ticker=symbol), conn=conn)
        for company_id, symbol in ((1, 'NVDA'), (2, 'OTHER')):
            for index in range(2):
                catalyst_id = f'catalyst:batch-{symbol}-{index}'
                with closing(get_connection(self.db)) as conn:
                    catalyst = dict(conn.execute('SELECT * FROM catalysts WHERE catalyst_id=?', (catalyst_id,)).fetchone())
                event = {'company': {'id': company_id, 'symbol': symbol, 'name': symbol}, 'catalyst_id': catalyst_id,
                         'evidence_revision': 'r1', 'package_id': None, 'articles': [], 'catalyst': catalyst,
                         'published_at': START, 'observed_at': ENTRY, 'change_kind': 'initial'}
                item = DiscoveryQueueItem(company_id=company_id, catalyst_id=catalyst_id, evidence_revision='r1',
                                          purpose='entry', origin='direct_event', created_at=ENTRY,
                                          priority_components={'materiality': 10})
                enqueue_discovery_event(item, event, expires_at=later(86400), db_path=self.db)

    def test_admission_takes_its_own_larger_batch(self):
        self.enqueue_four()
        policy = replace(self.policy, batch_size=2, direct_slots=1, admission_batch_size=4)
        result = discovery_queue.process_discovery_queue(policy=policy, clock=lambda: ENTRY, db_path=self.db,
                                                         version={'pipeline_version': 'discovery-test-v1'})
        self.assertEqual(len(result['candidates']), 4)

    def test_late_items_in_a_large_admission_batch_keep_a_valid_lease(self):
        self.enqueue_four()
        ticks = iter(range(0, 3600, 15))

        def clock():
            # Each queue step reads the clock; 15 s per read outlasts one 120 s lease over four items.
            return (datetime.fromisoformat(ENTRY) + timedelta(seconds=next(ticks))).isoformat()
        policy = replace(self.policy, batch_size=2, direct_slots=1, admission_batch_size=4, lease_seconds=120)
        result = discovery_queue.process_discovery_queue(policy=policy, clock=clock, db_path=self.db,
                                                         version={'pipeline_version': 'discovery-test-v1'})
        self.assertEqual((len(result['candidates']), result['failures']), (4, []))

    def test_an_admission_batch_smaller_than_the_research_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            DiscoveryPolicy(mode='shadow', batch_size=6, admission_batch_size=5)


class CollectorResearchCycleTests(DiscoveryFixture):
    def test_the_collector_cycle_runs_research_and_theses_in_the_research_batch(self):
        with patch.object(discovery_queue, 'process_research_queue', return_value={'sessions': []}) as research, \
                patch.object(discovery_queue, 'process_research_theses', return_value={'theses': []}) as theses:
            result = discovery_queue.run_discovery_research_cycle(policy=self.policy, clock=lambda: ENTRY, db_path=self.db)
        self.assertEqual((research.call_args.kwargs['limit'], theses.call_args.kwargs['limit']),
                         (self.policy.batch_size, self.policy.batch_size))
        self.assertEqual(set(result), {'research', 'theses'})

    def test_disabled_discovery_runs_no_research(self):
        with patch.object(discovery_queue, 'process_research_queue', side_effect=no_model_work):
            result = discovery_queue.run_discovery_research_cycle(policy=DiscoveryPolicy(), db_path=self.db)
        self.assertEqual(result['status'], 'disabled')


class PlannerWithoutFactsTests(DiscoveryFixture):
    def test_a_session_with_no_verified_company_facts_defers_without_a_planner_call(self):
        self.article()
        self.discover()
        candidate = self.process()['candidates'][0]
        planner = FakePlanner()
        result = ResearchOrchestrator(planner=planner, policy=EvidenceResearchPolicy(mode='shadow'),
                                      clock=lambda: ENTRY, db_path=self.db).run(candidate)
        self.assertEqual((result['status'], result['stop_reason'], len(planner.inputs)),
                         ('deferred_missing_evidence', 'verified_company_facts_unavailable', 0))
        self.assertTrue(result['evidence_refresh_requested'])


if __name__ == '__main__':
    unittest.main()
