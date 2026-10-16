"""Phase 3 acceptance: bounded investigation, auditability, recovery and isolation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import threading
import unittest
from unittest.mock import Mock, patch

from tests.test_event_discovery import DiscoveryFixture, later
from tests.test_company_evidence import COMPANY, guidance
from tests.research_foundation_fixtures import ENTRY
from db_common import get_connection
from market_db_modules.company_evidence import (
    get_company_fact,
    get_evidence_package,
    record_company_fact,
)
from market_db_modules.research_runtime import replay_research_session
from research_foundation_contracts import CompanyFact
from services.company_evidence_extraction import ingest_source
from services.evidence_research_policy import EvidenceResearchPolicy
from services.research_lock import ResearchSessionBusy
from services.research_orchestrator import ResearchOrchestrator
from services.research_tools import ResearchRequest, ResearchTools
from agent_stages.research_planner import ResearchPlanner, validate_plan


class FakePlanner:
    model = 'fixture-planner'
    prompt_version = 'fixture-v1'
    cost_per_call = 0

    def __init__(self, plans=None):
        self.plans = plans or [{'outcome': 'ready', 'reason': 'Enough evidence', 'requests': []}]
        self.inputs = []

    def plan(self, context, *, timeout):
        self.inputs.append(context)
        plan = self.plans[min(len(self.inputs) - 1, len(self.plans) - 1)]
        return plan(context) if callable(plan) else plan


class ResearchFixture(DiscoveryFixture):
    def setUp(self):
        super().setUp()
        self.package_id = self.package()
        self.discover()
        self.candidate = self.process()['candidates'][0]
        self.research_policy = EvidenceResearchPolicy(mode='shadow')
        self.planner = FakePlanner()
        package = get_evidence_package(self.candidate['research_input']['company_evidence']['package_id'], db_path=self.db)
        self.tools = ResearchTools(company_id=1, event_id=package['catalyst_id'],
                                   cutoff=ENTRY, db_path=self.db)

    def prior(self, **kwargs):
        source = guidance(80, 90, kind='prior', url='https://issuer.test/prior', **kwargs)
        return ingest_source(COMPANY, source, source.observed_at, db_path=self.db)

    def run_research(self, *, planner=None, policy=None, clock=lambda: ENTRY):
        return ResearchOrchestrator(planner=planner or self.planner, policy=policy or self.research_policy,
                                    clock=clock, db_path=self.db).run(self.candidate)

    def trace(self, result):
        return replay_research_session(result['session_id'], db_path=self.db)

    def request(self, operation='compare_guidance', **changes):
        args = {'company_id': 1, 'as_of': self.tools.cutoff}
        if operation in {'compare_guidance', 'get_available_consensus'}:
            args.update(metric='revenue', fiscal_period='FY2026-Q3')
        elif operation == 'get_company_facts':
            args.update(metrics=['revenue'], fiscal_periods=['FY2026-Q3'])
        else:
            args['event_id'] = self.tools.event_id
        return {'question': 'What was prior guidance?', 'operation': operation,
                'arguments': {**args, **changes}, 'critical': True}


class InvestigationTests(ResearchFixture):
    def test_unavailable_critical_evidence_defers(self):
        self.assertEqual(self.run_research()['status'], 'deferred_missing_evidence')

    def test_shadow_final_package_cannot_replace_legacy_latest_evidence(self):
        from market_db_modules.company_evidence import latest_evidence_package
        before = latest_evidence_package(1, as_of=ENTRY, db_path=self.db)['package_id']
        self.prior()
        self.run_research()
        self.assertEqual(latest_evidence_package(1, as_of=ENTRY, db_path=self.db)['package_id'], before)

    def test_retrievals_use_the_frozen_observation_cutoff(self):
        result = self.run_research()
        cutoffs = {step['request']['arguments']['as_of'] for step in self.trace(result)['steps']
                   if step['kind'] == 'retrieval_result'}
        self.assertEqual(cutoffs, {ENTRY})

    def test_future_prior_guidance_cannot_leak_into_session(self):
        self.prior(at=later(1, at=self.tools.cutoff))
        self.assertEqual(self.run_research()['status'], 'deferred_missing_evidence')

    def test_no_progress_stops_instead_of_replanning(self):
        self.planner = FakePlanner([{'outcome': 'continue', 'reason': 'Need a baseline', 'requests': []}])
        result = self.run_research()
        self.assertEqual(result['planner_calls'], 1)

    def test_tool_budget_counts_each_dispatch(self):
        self.prior()
        result = self.run_research(policy=replace(self.research_policy, max_tool_calls=1))
        self.assertEqual((result['status'], result['retrieval_calls']), ('budget_exhausted', 1))

    def test_daily_model_allowance_is_reserved_before_dispatch(self):
        self.prior()
        self.planner = FakePlanner([{'outcome': 'continue', 'reason': 'Investigate', 'requests': []}])
        result = self.run_research(policy=replace(self.research_policy, daily_planner_calls=1))
        self.assertEqual((result['stop_reason'], len(self.planner.inputs)), ('daily_planner_allowance', 1))

    def test_paid_adapter_is_denied_before_dispatch(self):
        self.planner.cost_per_call = .01
        result = self.run_research()
        self.assertEqual((result['status'], len(self.planner.inputs)), ('failed', 0))

    def test_invalid_model_tool_does_not_dispatch(self):
        bad = self.request(operation='compare_guidance', url='https://evil.test')
        self.planner = FakePlanner([{'outcome': 'continue', 'reason': 'Fetch', 'requests': [bad]}])
        self.assertEqual(self.run_research()['retrieval_calls'], 0)

    def test_default_mode_does_not_start_research(self):
        self.assertEqual(self.run_research(policy=EvidenceResearchPolicy())['status'], 'disabled')


class ValidationTests(ResearchFixture):
    def test_other_company_request_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'company mismatch'):
            self.tools.parse(self.request(company_id=2))

    def test_future_time_request_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cutoff'):
            self.tools.parse(self.request(as_of=later(1, at=self.tools.cutoff)))

    def test_arbitrary_operation_is_rejected(self):
        request = self.request()
        request['operation'] = 'submit_order'
        with self.assertRaisesRegex(ValueError, 'not approved'):
            self.tools.parse(request)

    def test_numeric_claim_without_source_support_is_rejected(self):
        _, ids = self.prior()
        fact = get_company_fact(ids[0], db_path=self.db)
        fact.update(fact_id='fact:unsupported', value=999, range_high=1000)
        record_company_fact(CompanyFact(**fact), db_path=self.db)
        with self.assertRaisesRegex(ValueError, 'not present'):
            self.tools.execute(self.tools.parse(self.request()), timeout=10)


class RecoveryTests(ResearchFixture):
    def test_completed_session_does_not_call_planner_again(self):
        self.prior()
        self.run_research()
        self.run_research()
        self.assertEqual(len(self.planner.inputs), 1)

    def test_completed_retrieval_is_not_repeated_after_interruption(self):
        self.prior()
        original = ResearchOrchestrator.retrieve
        def crash(worker):
            original(worker)
            raise KeyboardInterrupt()
        with patch.object(ResearchOrchestrator, 'retrieve', crash), self.assertRaises(KeyboardInterrupt):
            self.run_research()
        result = self.run_research()
        self.assertEqual((result['status'], result['planner_calls'], result['retrieval_calls']), ('ready', 1, 2))

    def test_unknown_inflight_call_is_not_paid_for_twice(self):
        self.planner.plan = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_research()
        result = self.run_research()
        self.assertEqual(result['stop_reason'], 'interrupted_call_outcome_unknown_no_automatic_retry')

    def test_concurrent_worker_cannot_duplicate_planning(self):
        entered, release = threading.Event(), threading.Event()
        def block(context):
            entered.set()
            release.wait(5)
            return {'outcome': 'ready', 'reason': 'Enough', 'requests': []}
        planner = FakePlanner([block])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.run_research, planner=planner)
            entered.wait(5)
            try:
                with self.assertRaises(ResearchSessionBusy):
                    self.run_research()
            finally:
                release.set()
                future.result()

    def test_daily_reservation_is_not_refunded_on_failure(self):
        self.planner.plan = Mock(side_effect=RuntimeError())
        self.run_research()
        with closing(get_connection(self.db)) as conn:
            calls = conn.execute("SELECT calls FROM research_daily_allowances WHERE kind='planner'").fetchone()[0]
        self.assertEqual(calls, 1)

    def test_research_steps_are_immutable(self):
        self.run_research()
        import sqlite3
        with closing(get_connection(self.db)) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM research_steps')


class PlannerContractTests(unittest.TestCase):
    def test_planner_rejects_credential_bearing_host(self):
        with self.assertRaises(ValueError):
            ResearchPlanner(model='fixture', host='http://secret:password@host')

    def test_terminal_plan_cannot_dispatch_operations(self):
        with self.assertRaises(ValueError):
            validate_plan({'outcome': 'ready', 'reason': 'Done', 'requests': [{}]})

    def test_live_mode_is_not_supported(self):
        with self.assertRaises(ValueError):
            EvidenceResearchPolicy(mode='live')


if __name__ == '__main__':
    unittest.main()
