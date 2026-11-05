"""Demand-driven collection and immutable research continuation, entirely offline."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from unittest.mock import Mock, patch

from tests.test_research_orchestrator import ResearchFixture, FakePlanner
from tests.test_event_discovery import DiscoveryFixture, later
from tests.test_company_evidence import COMPANY, actuals, guidance
from tests.research_foundation_fixtures import ENTRY
from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_evidence import get_evidence_package
from market_db_modules.evidence_refresh import (
    due_refresh_requests,
    finish_refresh_requests,
    request_company_evidence,
)
from market_db_modules.research_runtime import replay_research_session
from services.discovery.company_evidence_demand import request_pipeline_evidence
from services.discovery.company_evidence_policy import CompanyEvidencePolicy
from services.discovery.company_evidence_service import collect_company_evidence
from services.research.research_continuation import prepare_research_continuations
from services.research.research_orchestrator import process_research_queue
from Webscraping.company_evidence.models import CollectionResult


class CollectorMixin:
    def collector(self, *, at=ENTRY, sources=None, error=None, mapping=None):
        self.adapter = Mock(name='sec-fixture')
        self.adapter.name = 'sec'
        self.adapter.issuer_map.return_value = mapping if mapping is not None else {'NVDA': COMPANY['cik']}
        self.adapter.collect.return_value = CollectionResult(tuple(sources or [guidance()]), {})
        self.adapter.collect.side_effect = error
        with patch('services.discovery.company_evidence_service.SECAdapter', return_value=self.adapter), \
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

    def test_pipeline_requests_deduplicate_within_refresh_window(self):
        for _ in range(2):
            request_pipeline_evidence(['NVDA', 'NVDA'], at=ENTRY, db_path=self.db)
        self.assertEqual(len(self.requests()), 1)

    def test_opportunist_persistence_queues_company_atomically(self):
        from news_db_modules.impacts import add_company_opportunist_impact
        self.article()
        with closing(get_connection(self.db)) as conn, conn:
            add_company_opportunist_impact(1, 1, confidence='high', conn=conn)
        self.assertEqual([(r['company_id'], r['source']) for r in self.requests()], [(1, 'opportunist')])

    def test_duplicate_concurrent_requests_have_one_identity(self):
        def submit(_):
            return request_company_evidence(1, 'fixture', 'same', at=ENTRY, db_path=self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            identities = list(pool.map(submit, range(4)))
        self.assertEqual((len(set(identities)), len(self.requests())), (1, 1))

    def test_same_company_requests_share_one_provider_fetch(self):
        for key in ('a', 'b'):
            request_company_evidence(1, 'fixture', key, at=ENTRY, db_path=self.db)
        self.collector()
        self.assertEqual((self.adapter.collect.call_count, {r['status'] for r in self.requests()}), (1, {'completed'}))

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


class ResearchRefreshTests(CollectorMixin, ResearchFixture):
    def collect_missing_baseline(self, at=None):
        at = at or later(10)
        return self.collector(at=at, sources=[guidance(80, 90, kind='prior', at=at,
                                                       url='https://issuer.test/prior')])

    def continue_research(self, at=None):
        return process_research_queue(policy=self.research_policy, planner=FakePlanner(),
                                      clock=lambda: at or later(11), db_path=self.db)

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

    def test_continuation_is_idempotent_across_restarts(self):
        self.run_research()
        self.collect_missing_baseline()
        self.continue_research()
        initialize_database(db_path=self.db)
        self.assertEqual(self.continue_research()['sessions'], [])

    def test_incomplete_followup_does_not_create_unbounded_refresh_chain(self):
        self.run_research()
        self.collector(at=later(10), sources=[actuals(120, at=later(10))])
        result = self.continue_research()['sessions'][0]
        self.assertEqual((result['status'], len([r for r in self.requests() if r['source'] == 'research_gap'])),
                         ('deferred_missing_evidence', 1))

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
