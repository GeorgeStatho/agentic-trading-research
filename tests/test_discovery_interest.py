"""Interest-driven discovery notifications, dependency wakeups and research ownership."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from unittest.mock import patch

from tests.test_event_discovery import DiscoveryFixture, later
from tests.test_evidence_refresh import CollectorMixin
from tests.test_research_orchestrator import ResearchFixture, FakePlanner
from tests.test_company_evidence import guidance
from tests.research_foundation_fixtures import START, ENTRY
from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_interest import (
    active_company_ids, register_interest, signal_discovery, signal_company_news,
    due_discovery_checks, finish_discovery_check,
)
from market_db_modules.discovery_queue import discovery_queue_status
from market_db_modules.research_runtime import replay_research_session
from services.company_evidence_demand import request_pipeline_evidence
from services.discovery_policy import DiscoveryPolicy
from services.research_continuation import prepare_research_continuations
from services.research_orchestrator import process_research_queue


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

    def test_environment_symbols_are_obsolete(self):
        with patch.dict('os.environ', {'DISCOVERY_SYMBOLS': '*'}):
            self.assertEqual(DiscoveryPolicy.from_env().symbols, ())

    def test_shared_interest_does_not_need_a_collection_request(self):
        self.select()
        self.article()
        self.assertTrue(self.discover()['queue_ids'])

    def test_replaced_pipeline_selection_removes_old_company(self):
        request_pipeline_evidence(['NVDA'], at=START, db_path=self.db)
        request_pipeline_evidence(['OTHER'], at=ENTRY, db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            self.assertEqual(active_company_ids(conn, at=ENTRY), {2})

    def test_empty_selection_clears_its_scope(self):
        request_pipeline_evidence(['NVDA'], at=START, db_path=self.db)
        request_pipeline_evidence([], at=ENTRY, db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            self.assertEqual(active_company_ids(conn, at=ENTRY), set())

    def test_independent_selection_scopes_do_not_remove_each_other(self):
        request_pipeline_evidence(['NVDA'], scope='manager', at=ENTRY, db_path=self.db)
        request_pipeline_evidence(['OTHER'], scope='industry', at=ENTRY, db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            self.assertEqual(active_company_ids(conn, at=ENTRY), {1, 2})

    def test_repeated_selection_does_not_wake_an_unchanged_company(self):
        request_pipeline_evidence(['NVDA'], at=ENTRY, db_path=self.db)
        self.discover()
        request_pipeline_evidence(['NVDA'], at=later(1), db_path=self.db)
        self.assertEqual(self.discover(at=later(1))['scanned_company_ids'], [])

    def test_collection_completion_does_not_remove_company_interest(self):
        request_pipeline_evidence(['NVDA'], at=START, db_path=self.db)
        self.collector()
        with closing(get_connection(self.db)) as conn:
            self.assertEqual(active_company_ids(conn, at=ENTRY), {1})

    def test_news_for_an_unselected_company_does_not_enroll_it(self):
        self.article()
        with closing(get_connection(self.db)) as conn, conn:
            signal_company_news(conn, 1, at=ENTRY)
        self.assertIsNone(self.check())

    def test_expired_interest_receives_no_checks(self):
        self.select(lifetime=60)
        self.assertEqual(self.discover()['scanned_company_ids'], [])

    def test_periodic_checks_do_not_extend_interest_expiry(self):
        self.select(at=ENTRY, lifetime=301)
        self.discover()
        self.discover(at=later(300))
        self.assertEqual(self.discover(at=later(302))['scanned_company_ids'], [])

    def test_idle_inputs_wait_until_periodic_check(self):
        self.select()
        self.discover()
        self.assertEqual(self.discover(at=later(1))['scanned_company_ids'], [])

    def test_periodic_check_recovers_a_missed_news_signal(self):
        self.select()
        self.article()
        self.discover()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE news_articles SET body='A material correction' WHERE id=1")
        self.discover(at=later(301))
        self.assertEqual(len(discovery_queue_status(db_path=self.db)), 2)

    def test_news_update_wakes_check_before_periodic_deadline(self):
        self.select()
        self.article()
        self.discover()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE news_articles SET body='A material correction' WHERE id=1")
            signal_company_news(conn, 1, at=later(1))
        self.assertEqual(self.discover(at=later(1))['scanned_company_ids'], [1])

    def test_repeated_identical_notification_is_coalesced(self):
        self.select()
        with closing(get_connection(self.db)) as conn, conn:
            for _ in range(3):
                signal_discovery(conn, 1, 'fixture', 'same', at=ENTRY)
        self.assertEqual(self.check()['generation'], 2)

    def test_new_signal_during_a_check_survives_acknowledgement(self):
        self.select()
        captured = due_discovery_checks(at=ENTRY, limit=1, db_path=self.db)[0]
        with closing(get_connection(self.db)) as conn, conn:
            signal_discovery(conn, 1, 'fixture', 'new', at=later(1))
        finish_discovery_check(captured, at=later(2), next_at=later(302), reason='checked', db_path=self.db)
        self.assertEqual(self.check()['status'], 'pending')

    def test_repeated_delivery_keeps_original_waiting_age(self):
        self.select()
        with closing(get_connection(self.db)) as conn, conn:
            signal_discovery(conn, 1, 'fixture', 'new', at=ENTRY)
        self.assertEqual(self.check()['requested_at'], START)

    def test_interest_without_evidence_waits_without_inventing_a_catalyst(self):
        self.select()
        result = self.discover()
        self.assertEqual((result['queue_ids'], self.check()['status']), ([], 'waiting_for_evidence'))

    def test_missing_evidence_reuses_one_collection_dependency(self):
        self.select()
        self.discover()
        dependency = self.check()['dependency_request_id']
        self.discover(at=later(301))
        self.assertEqual(self.check()['dependency_request_id'], dependency)

    def test_collected_evidence_wakes_discovery_and_creates_candidate(self):
        self.select()
        self.discover()
        self.collector(at=later(1), sources=[guidance(at=later(1))])
        self.discover(at=later(2))
        self.assertEqual(len(self.process(at=later(2))['candidates']), 1)

    def test_unchanged_evidence_does_not_create_another_event_revision(self):
        self.select()
        self.package()
        self.discover()
        self.package(at=START)
        self.discover(at=later(301))
        self.assertEqual(len(discovery_queue_status(db_path=self.db)), 1)

    def test_two_workers_cannot_check_the_same_batch(self):
        from services.research_lock import research_session_lock
        self.select()
        with research_session_lock(self.db, 'company-discovery'):
            self.assertEqual(self.discover()['status'], 'busy')

    def test_bootstrap_preserves_pending_notifications(self):
        self.select()
        initialize_database(db_path=self.db)
        self.assertEqual(self.discover()['scanned_company_ids'], [1])

    def test_scan_limit_leaves_other_selected_companies_pending(self):
        self.select(1)
        self.select(2)
        self.policy = replace(self.policy, scan_limit=1)
        self.discover()
        self.assertEqual(self.discover(at=later(1))['scanned_company_ids'], [2])

    def test_closed_position_stops_discovery_when_no_other_interest_exists(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO position_theses(symbol,option_symbol,opened_at) VALUES ('NVDA','NVDA-OPTION',?)", (START,))
        self.discover()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE position_theses SET status='closed'")
        self.assertEqual(self.discover(at=later(301))['scanned_company_ids'], [])

    def test_watchlist_update_wakes_discovery(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO strategist_company_summaries(company_id,decision,summary,updated_at) VALUES (1,'watchlist','Initial',?)", (START,))
        self.discover()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE strategist_company_summaries SET summary='Changed reason',updated_at=?", (later(1),))
        self.assertEqual(self.discover(at=later(1))['scanned_company_ids'], [1])

    def test_migration_preserves_original_request_age(self):
        request_pipeline_evidence(['NVDA'], at=START, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('DELETE FROM company_interest')
        self.discover()
        with closing(get_connection(self.db)) as conn:
            selected = conn.execute('SELECT MIN(selected_at) FROM company_interest').fetchone()[0]
        self.assertEqual(selected, START)


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

    def test_discovery_then_continuation_runs_one_successor(self):
        self.run_research()
        self.new_baseline()
        self.discover(at=later(11))
        self.process(at=later(11))
        result, planner = self.run_pending()
        self.assertEqual(([s['status'] for s in result['sessions']], len(planner.inputs)), (['ready'], 1))

    def test_parent_replay_is_unchanged_when_routes_converge(self):
        parent = self.run_research()
        original = replay_research_session(parent['session_id'], db_path=self.db)
        self.new_baseline()
        prepare_research_continuations(limit=6, db_path=self.db)
        self.discover(at=later(11))
        self.process(at=later(11))
        self.run_pending()
        self.assertEqual(replay_research_session(parent['session_id'], db_path=self.db), original)

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
