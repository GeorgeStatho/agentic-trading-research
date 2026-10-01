"""Demand-driven collection and immutable research continuation, entirely offline."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from unittest.mock import Mock, patch

from tests.test_research_orchestrator import ResearchFixture, FakePlanner
from tests.test_event_discovery import DiscoveryFixture, later
from tests.test_company_evidence import COMPANY, actuals, guidance
from tests.research_foundation_fixtures import ENTRY, START
from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_evidence import get_evidence_package
from market_db_modules.evidence_refresh import (
    due_refresh_companies, due_refresh_requests, finish_refresh_requests, request_company_evidence,
)
from market_db_modules.research_observations import get_research_observation
from market_db_modules.research_runtime import replay_research_session
from services.company_evidence_demand import request_pipeline_evidence
from services.company_evidence_policy import CompanyEvidencePolicy
from services.company_evidence_service import collect_company_evidence
from services.research_continuation import prepare_research_continuations
from services.research_orchestrator import process_research_queue
from Webscraping.company_evidence.models import CollectionResult


class CollectorMixin:
    def collector(self, *, at=ENTRY, sources=None, error=None, mapping=None):
        self.adapter = Mock(name='sec-fixture')
        self.adapter.name = 'sec'
        self.adapter.issuer_map.return_value = mapping if mapping is not None else {'NVDA': COMPANY['cik']}
        self.adapter.collect.return_value = CollectionResult(tuple(sources or [guidance()]), {})
        self.adapter.collect.side_effect = error
        with patch('services.company_evidence_service.SECAdapter', return_value=self.adapter), \
             patch.dict('os.environ', {'SEC_USER_AGENT': 'Fixture contact@example.test',
                                      'COMPANY_EVIDENCE_SYMBOLS': '', 'COMPANY_EVIDENCE_ISSUERS_FILE': '',
                                      'DISCOVERY_MODE': 'disabled', 'DISCOVERY_SYMBOLS': ''}):
            return collect_company_evidence(db_path=self.db, policy=CompanyEvidencePolicy(enabled=True),
                                            clock=lambda: datetime.fromisoformat(at))

    def requests(self):
        with closing(get_connection(self.db)) as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM company_evidence_requests ORDER BY request_id')]


class EvidenceDemandTests(CollectorMixin, DiscoveryFixture):
    interested_symbols = ()

    def test_no_interest_means_no_provider_call_even_with_database_companies(self):
        result = self.collector()
        self.assertEqual((result['status'], self.adapter.issuer_map.call_count), ('idle', 0))

    def test_actual_pipeline_selection_collects_only_selected_company_without_symbols(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        self.assertEqual([c['symbol'] for c in self.collector()['companies']], ['NVDA'])

    def test_pipeline_requests_deduplicate_within_refresh_window(self):
        for _ in range(2):
            request_pipeline_evidence(['NVDA', 'NVDA'], at=ENTRY, db_path=self.db)
        self.assertEqual(len(self.requests()), 1)

    def test_new_pipeline_window_can_request_a_later_refresh(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        request_pipeline_evidence(['NVDA'], at=later(10801), db_path=self.db)
        self.assertEqual(len(self.requests()), 2)

    def test_unknown_symbol_does_not_become_a_provider_target(self):
        self.assertEqual(request_pipeline_evidence(['INVENTED'], at=ENTRY, db_path=self.db), [])

    def test_opportunist_persistence_queues_company_atomically(self):
        from news_db_modules.impacts import add_company_opportunist_impact
        self.article()
        with closing(get_connection(self.db)) as conn, conn:
            add_company_opportunist_impact(1, 1, confidence='high', conn=conn)
        self.assertEqual([(r['company_id'], r['source']) for r in self.requests()], [(1, 'opportunist')])

    def test_rolled_back_opportunist_finding_does_not_leave_a_request(self):
        from news_db_modules.impacts import add_company_opportunist_impact
        self.article()
        with closing(get_connection(self.db)) as conn:
            conn.execute('BEGIN')
            add_company_opportunist_impact(1, 1, confidence='high', conn=conn)
            conn.rollback()
        self.assertEqual(self.requests(), [])

    def test_duplicate_concurrent_requests_have_one_identity(self):
        def submit(_):
            return request_company_evidence(1, 'fixture', 'same', at=ENTRY, db_path=self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            identities = list(pool.map(submit, range(4)))
        self.assertEqual((len(set(identities)), len(self.requests())), (1, 1))

    def test_company_limit_bounds_provider_targets(self):
        for identity in (1, 2):
            request_company_evidence(identity, 'fixture', str(identity), at=ENTRY, db_path=self.db)
        self.assertEqual(len(due_refresh_companies(at=ENTRY, limit=1, db_path=self.db)), 1)

    def test_queue_survives_bootstrap_and_restart(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        initialize_database(db_path=self.db)
        self.assertEqual(len(due_refresh_companies(at=ENTRY, limit=5, db_path=self.db)), 1)

    def test_same_company_requests_share_one_provider_fetch(self):
        for key in ('a', 'b'):
            request_company_evidence(1, 'fixture', key, at=ENTRY, db_path=self.db)
        self.collector()
        self.assertEqual((self.adapter.collect.call_count, {r['status'] for r in self.requests()}), (1, {'completed'}))

    def test_provider_failure_retains_request_with_backoff(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        self.collector(error=RuntimeError('offline'))
        request = self.requests()[0]
        self.assertTrue(request['status'] == 'pending' and request['next_attempt_at'] > ENTRY)

    def test_three_failed_attempts_stop_automatic_collection(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        for seconds in (0, 121, 362):
            self.collector(at=later(seconds), mapping={})
        self.assertEqual(self.requests()[0]['status'], 'unavailable')

    def test_new_arrival_during_collection_remains_pending(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        captured = due_refresh_requests(1, at=ENTRY, db_path=self.db)
        request_company_evidence(1, 'fixture', 'later-arrival', at=later(1), db_path=self.db)
        finish_refresh_requests(captured, at=later(2), reason='fixture_failure', db_path=self.db)
        self.assertEqual([r['attempts'] for r in self.requests() if r['source'] == 'fixture'], [0])

    def test_old_pipeline_interest_expires_without_fetching(self):
        request_pipeline_evidence(['NVDA'], at=START, db_path=self.db)
        result = self.collector(at=later(8 * 86400))
        self.assertEqual((result['status'], self.requests()[0]['reason']), ('idle', 'interest_expired'))

    def test_disabled_collection_preserves_pending_request(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        collect_company_evidence(db_path=self.db, policy=CompanyEvidencePolicy())
        self.assertEqual(self.requests()[0]['status'], 'pending')

    def test_selected_company_enters_discovery_without_manual_universe(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        from market_db_modules.discovery_sources import select_discovery_companies
        result = select_discovery_companies(replace(self.policy, symbols=()), at=ENTRY, db_path=self.db)
        self.assertEqual([c['symbol'] for c in result], ['NVDA'])

    def test_collector_lock_prevents_concurrent_provider_work(self):
        from services.research_lock import research_session_lock
        with research_session_lock(self.db, 'company-evidence-collector'):
            result = self.collector()
        self.assertEqual((result['status'], self.adapter.issuer_map.call_count), ('busy', 0))


class ResearchRefreshTests(CollectorMixin, ResearchFixture):
    def collect_missing_baseline(self, at=None):
        at = at or later(10)
        return self.collector(at=at, sources=[guidance(80, 90, kind='prior', at=at,
                                                       url='https://issuer.test/prior')])

    def continue_research(self, at=None):
        return process_research_queue(policy=self.research_policy, planner=FakePlanner(),
                                      clock=lambda: at or later(11), db_path=self.db)

    def test_missing_evidence_finishes_with_persistent_refresh_request(self):
        result = self.run_research()
        self.assertEqual([r['parent_session_id'] for r in self.requests() if r['source'] == 'research_gap'],
                         [result['session_id']])

    def test_refresh_resolves_gap_in_a_distinct_session(self):
        original = self.run_research()
        self.collect_missing_baseline()
        result = self.continue_research()['sessions'][0]
        self.assertEqual((result['status'], result['continuation']['parent_session_id']), ('ready', original['session_id']))

    def test_original_session_and_journal_are_unchanged_after_continuation(self):
        original = self.run_research()
        before = replay_research_session(original['session_id'], db_path=self.db)
        self.collect_missing_baseline()
        self.continue_research()
        self.assertEqual(replay_research_session(original['session_id'], db_path=self.db), before)

    def test_new_observation_cutoff_includes_later_collected_evidence(self):
        self.run_research()
        self.collect_missing_baseline()
        result = self.continue_research()['sessions'][0]
        trace = replay_research_session(result['session_id'], db_path=self.db)
        observation = get_research_observation(trace['initial_input']['observation_id'], db_path=self.db)
        self.assertEqual(observation['information_cutoff_time'], later(10))

    def test_continuation_is_idempotent_across_restarts(self):
        self.run_research()
        self.collect_missing_baseline()
        self.continue_research()
        initialize_database(db_path=self.db)
        self.assertEqual(self.continue_research()['sessions'], [])

    def test_unchanged_evidence_does_not_trigger_another_model_run(self):
        self.run_research()
        self.collector(at=later(10))
        self.assertEqual(self.continue_research()['sessions'], [])

    def test_cached_refresh_waits_for_provider_ttl(self):
        self.collector(at=ENTRY)
        self.run_research()
        self.collect_missing_baseline()
        request = next(r for r in self.requests() if r['source'] == 'research_gap')
        self.assertEqual((request['status'], request['attempts'], self.adapter.collect.call_count), ('pending', 0, 0))

    def test_incomplete_followup_does_not_create_unbounded_refresh_chain(self):
        self.run_research()
        self.collector(at=later(10), sources=[actuals(120, at=later(10))])
        result = self.continue_research()['sessions'][0]
        self.assertEqual((result['status'], len([r for r in self.requests() if r['source'] == 'research_gap'])),
                         ('deferred_missing_evidence', 1))

    def test_stale_input_requests_refresh_before_using_planner(self):
        self.candidate['research_input']['company_evidence']['freshness_at_read'] = 'stale'
        result = self.run_research()
        self.assertEqual((result['stop_reason'], len(self.planner.inputs), result['evidence_refresh_requested']),
                         ('primary_evidence_stale', 0, True))

    def test_new_event_revision_prevents_old_event_continuation(self):
        self.run_research()
        self.collect_missing_baseline()
        self.discover(at=later(11))
        prepare_research_continuations(limit=6, db_path=self.db)
        request = next(r for r in self.requests() if r['source'] == 'research_gap')
        self.assertEqual(request['reason'], 'event_superseded')

    def test_successor_respects_shared_daily_model_allowance(self):
        self.run_research(policy=replace(self.research_policy, daily_planner_calls=1))
        self.collect_missing_baseline()
        result = process_research_queue(policy=replace(self.research_policy, daily_planner_calls=1),
                                        planner=FakePlanner(), clock=lambda: later(11), db_path=self.db)['sessions'][0]
        self.assertEqual((result['status'], result['planner_calls']), ('budget_exhausted', 0))

    def test_disabled_research_does_not_run_continuation(self):
        self.run_research()
        self.collect_missing_baseline()
        planner = FakePlanner()
        process_research_queue(policy=replace(self.research_policy, mode='disabled'),
                               planner=planner, clock=lambda: later(11), db_path=self.db)
        self.assertEqual(planner.inputs, [])

    def test_concurrent_continuation_preparation_creates_one_successor(self):
        self.run_research()
        self.collect_missing_baseline()
        def prepare(_):
            return prepare_research_continuations(limit=6, db_path=self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            failures = list(pool.map(prepare, range(2)))
        with closing(get_connection(self.db)) as conn:
            count = conn.execute('SELECT COUNT(*) FROM research_evidence_continuations').fetchone()[0]
        self.assertEqual((failures, count), ([[], []], 1))
