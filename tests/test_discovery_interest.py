"""Interest-driven discovery notifications, dependency wakeups and research ownership."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace

from tests.test_event_discovery import DiscoveryFixture, later
from tests.test_evidence_refresh import CollectorMixin
from tests.test_research_orchestrator import ResearchFixture, FakePlanner
from tests.test_company_evidence import guidance
from tests.research_foundation_fixtures import START, ENTRY
from db_common import get_connection
from market_db_modules.company_interest import (
    active_company_ids,
    register_interest,
    signal_discovery,
    due_discovery_checks,
    finish_discovery_check,
)
from services.discovery.company_evidence_demand import request_pipeline_evidence
from services.research.research_continuation import prepare_research_continuations
from services.research.research_orchestrator import process_research_queue


class InterestDiscoveryTests(CollectorMixin, DiscoveryFixture):
    interested_symbols = ()

    def select(self, company_id=1, *, at=START, lifetime=604800):
        with closing(get_connection(self.db)) as conn, conn:
            register_interest(conn, company_id, 'pipeline_selection', 'current', at=at, lifetime_seconds=lifetime)

    def check(self):
        with closing(get_connection(self.db)) as conn:
            row = conn.execute('SELECT * FROM discovery_checks WHERE company_id=1').fetchone()
            return dict(row) if row else None

    def test_manual_wildcard_does_not_enroll_database_companies(self):
        self.policy = replace(self.policy, symbols=('*',))
        self.assertEqual(self.discover()['scanned_company_ids'], [])

    def test_replaced_pipeline_selection_removes_old_company(self):
        request_pipeline_evidence(['NVDA'], at=START, db_path=self.db)
        request_pipeline_evidence(['OTHER'], at=ENTRY, db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            self.assertEqual(active_company_ids(conn, at=ENTRY), {2})

    def test_expired_interest_receives_no_checks(self):
        self.select(lifetime=60)
        self.assertEqual(self.discover()['scanned_company_ids'], [])

    def test_new_signal_during_a_check_survives_acknowledgement(self):
        self.select()
        captured = due_discovery_checks(at=ENTRY, limit=1, db_path=self.db)[0]
        with closing(get_connection(self.db)) as conn, conn:
            signal_discovery(conn, 1, 'fixture', 'new', at=later(1))
        finish_discovery_check(captured, at=later(2), next_at=later(302), reason='checked', db_path=self.db)
        self.assertEqual(self.check()['status'], 'pending')

    def test_collected_evidence_wakes_discovery_and_creates_candidate(self):
        self.select()
        self.discover()
        self.collector(at=later(1), sources=[guidance(at=later(1))])
        self.discover(at=later(2))
        self.assertEqual(len(self.process(at=later(2))['candidates']), 1)

    def test_closed_position_stops_discovery_when_no_other_interest_exists(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO position_theses(symbol,option_symbol,opened_at) VALUES ('NVDA','NVDA-OPTION',?)", (START,))
        self.discover()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE position_theses SET status='closed'")
        self.assertEqual(self.discover(at=later(301))['scanned_company_ids'], [])


class ResearchOwnershipTests(CollectorMixin, ResearchFixture):
    def new_baseline(self):
        self.collector(at=later(10), sources=[guidance(80, 90, kind='prior', at=later(10), url='https://issuer.test/prior')])

    def run_pending(self):
        planner = FakePlanner()
        result = process_research_queue(policy=self.research_policy, planner=planner, clock=lambda: later(12), db_path=self.db)
        return result, planner

    def test_continuation_then_discovery_runs_one_successor(self):
        self.run_research()
        self.new_baseline()
        prepare_research_continuations(limit=6, db_path=self.db)
        self.discover(at=later(11))
        self.process(at=later(11))
        result, planner = self.run_pending()
        self.assertEqual(([s['status'] for s in result['sessions']], len(planner.inputs)), (['ready'], 1))

    def test_research_owner_is_claimed_atomically(self):
        from market_db_modules.research_work import claim_research_work
        item = self.candidate['research_input']['company_evidence']
        def claim(session):
            with closing(get_connection(self.db)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                return claim_research_work(conn, {**self.candidate, 'purpose': 'watchlist'}, 1, item['catalyst_id'], session, at=ENTRY)
        with ThreadPoolExecutor(max_workers=2) as pool:
            owners = list(pool.map(claim, ['worker-a', 'worker-b']))
        self.assertEqual(len(set(owners)), 1)
