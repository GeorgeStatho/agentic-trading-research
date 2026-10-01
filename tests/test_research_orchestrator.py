"""Phase 3 acceptance: bounded investigation, auditability, recovery and isolation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
import threading
import unittest
from unittest.mock import Mock, patch

from tests.test_event_discovery import DiscoveryFixture, later
from tests.test_company_evidence import COMPANY, guidance
from tests.research_foundation_fixtures import START, ENTRY
from db_common import get_connection
from market_db_modules.company_evidence import get_company_fact, get_evidence_document, get_evidence_package, record_company_fact
from market_db_modules.discovery_queue import enqueue_discovery_event
from market_db_modules.research_foundation import get_discovery_item
from market_db_modules.research_runtime import replay_research_session
from research_foundation_contracts import CompanyFact, DiscoveryQueueItem
from services.company_evidence_extraction import ingest_source
from services.evidence_research_policy import EvidenceResearchPolicy
from services.research_lock import ResearchSessionBusy
from services.research_orchestrator import ResearchOrchestrator, process_research_queue
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
    def test_missing_prior_guidance_produces_retrieval_request(self):
        result = self.run_research()
        requests = [s['request']['operation'] for s in self.trace(result)['steps'] if s['kind'] == 'retrieval_result']
        self.assertIn('compare_guidance', requests)

    def test_unavailable_critical_evidence_defers(self):
        self.assertEqual(self.run_research()['status'], 'deferred_missing_evidence')

    def test_archived_prior_guidance_completes_initial_package(self):
        _, facts = self.prior()
        result = self.run_research()
        final = get_evidence_package(result['final_package_id'], db_path=self.db)
        self.assertTrue(result['status'] == 'ready' and set(facts) <= set(final['fact_ids']))

    def test_initial_observation_remains_frozen(self):
        before = json.dumps(self.candidate, sort_keys=True)
        self.prior()
        self.run_research()
        self.assertEqual(json.dumps(self.candidate, sort_keys=True), before)

    def test_historical_guidance_can_supply_prior_forecast(self):
        old = guidance(80, 90, at=later(-86400, at=START), url='https://issuer.test/earlier-forecast')
        ingest_source(COMPANY, old, old.observed_at, db_path=self.db)
        self.assertEqual(self.run_research()['status'], 'ready')

    def test_ambiguous_historical_baselines_do_not_become_ready(self):
        for value in (70, 80):
            old = guidance(value, value + 5, at=later(-86400, at=START), url=f'https://issuer.test/old-{value}')
            ingest_source(COMPANY, old, old.observed_at, db_path=self.db)
        self.assertEqual(self.run_research()['status'], 'deferred_missing_evidence')

    def test_daily_retrieval_allowance_is_reserved_before_dispatch(self):
        self.prior()
        result = self.run_research(policy=replace(self.research_policy, daily_retrieval_calls=1))
        self.assertEqual((result['stop_reason'], result['retrieval_calls']), ('daily_retrieval_allowance', 1))

    def test_wall_deadline_stops_after_slow_planner(self):
        self.prior()
        now = [ENTRY]
        def slow(context):
            now[0] = later(91)
            return {'outcome': 'ready', 'reason': 'Enough', 'requests': []}
        result = self.run_research(planner=FakePlanner([slow]), clock=lambda: now[0])
        self.assertEqual((result['status'], result['retrieval_calls']), ('budget_exhausted', 0))

    def test_nonfinite_planner_output_is_recorded_as_failure(self):
        plan = {'outcome': 'continue', 'reason': 'Investigate', 'requests': [self.request(company_id=float('nan'))]}
        self.assertEqual(self.run_research(planner=FakePlanner([plan]))['status'], 'failed')

    def test_shadow_final_package_cannot_replace_legacy_latest_evidence(self):
        from market_db_modules.company_evidence import latest_evidence_package
        before = latest_evidence_package(1, as_of=ENTRY, db_path=self.db)['package_id']
        self.prior()
        self.run_research()
        self.assertEqual(latest_evidence_package(1, as_of=ENTRY, db_path=self.db)['package_id'], before)

    def test_shadow_research_does_not_create_discovery_feedback(self):
        self.prior()
        self.run_research()
        from services.event_discovery import company_discovery_events
        events = company_discovery_events(COMPANY, at=ENTRY, policy=self.policy, db_path=self.db)
        self.assertEqual(events[0]['package_id'], self.candidate['research_input']['company_evidence']['package_id'])

    def test_research_result_has_no_trade_permission_fields(self):
        self.prior()
        result = self.run_research()
        self.assertFalse({'recommendation', 'selected_option', 'allow_trade'} & result.keys())

    def test_initial_deferral_still_searches_for_missing_baseline(self):
        self.prior()
        plans = [{'outcome': 'deferred_missing_evidence', 'reason': 'Missing prior guidance', 'requests': []},
                 {'outcome': 'ready', 'reason': 'Retrieved baseline resolves the gap', 'requests': []}]
        result = self.run_research(planner=FakePlanner(plans))
        self.assertEqual((result['status'], result['planner_calls']), ('ready', 2))

    def test_evidence_collected_between_package_and_candidate_cutoffs_is_available(self):
        self.prior(at=later(1, at=START))
        self.assertEqual(self.run_research()['status'], 'ready')

    def test_retrievals_use_the_frozen_observation_cutoff(self):
        result = self.run_research()
        cutoffs = {step['request']['arguments']['as_of'] for step in self.trace(result)['steps']
                   if step['kind'] == 'retrieval_result'}
        self.assertEqual(cutoffs, {ENTRY})

    def test_exhausted_questions_stop_with_no_progress(self):
        self.prior()
        planner = FakePlanner([{'outcome': 'continue', 'reason': 'Investigate', 'requests': []}])
        self.assertEqual(self.run_research(planner=planner)['stop_reason'], 'no_progress')

    def test_other_period_cannot_supply_prior_guidance(self):
        self.prior(period='Q2 FY2026')
        self.assertEqual(self.run_research()['status'], 'deferred_missing_evidence')

    def test_future_prior_guidance_cannot_leak_into_session(self):
        self.prior(at=later(1, at=self.tools.cutoff))
        self.assertEqual(self.run_research()['status'], 'deferred_missing_evidence')

    def test_independent_conflicting_sources_are_preserved(self):
        self.prior()
        source = guidance(130, 140, url='https://issuer.test/conflict')
        ingest_source(COMPANY, source, START, db_path=self.db)
        result = self.run_research()
        trace = self.trace(result)
        self.assertTrue(result['status'] == 'conflicting_evidence' and bool(trace['evidence']['conflicts']))

    def test_provider_failure_is_durable(self):
        self.planner.plan = Mock(side_effect=RuntimeError('Provider down'))
        result = self.run_research()
        self.assertEqual((result['status'], result['planner_calls']), ('failed', 1))

    def test_planner_timeout_is_budget_exhausted(self):
        self.planner.plan = Mock(side_effect=TimeoutError())
        self.assertEqual(self.run_research()['stop_reason'], 'deadline_exhausted')

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

    def test_round_budget_bounds_planner_calls(self):
        self.prior()
        self.planner = FakePlanner([{'outcome': 'continue', 'reason': 'Investigate', 'requests': []}])
        result = self.run_research(policy=replace(self.research_policy, max_rounds=1))
        self.assertEqual(result['stop_reason'], 'planner_call_budget')

    def test_repeated_operation_stops_without_second_retrieval(self):
        self.prior()
        self.planner = FakePlanner([{'outcome': 'continue', 'reason': 'Investigate', 'requests': []},
                                    {'outcome': 'continue', 'reason': 'Again', 'requests': [self.request()]}])
        self.assertEqual(self.run_research()['stop_reason'], 'repeated_request')

    def test_superseding_event_during_planning_stops_session(self):
        def supersede(context):
            item = get_discovery_item(self.candidate['queue_id'], db_path=self.db)
            with closing(get_connection(self.db)) as conn:
                row = conn.execute('SELECT payload_json FROM discovery_events WHERE company_id=? AND catalyst_id=?',
                                   (1, item['catalyst_id'])).fetchone()
            event = json.loads(row[0])
            event.update(evidence_revision='new-revision', package_id=None, observed_at=later(1))
            enqueue_discovery_event(DiscoveryQueueItem(company_id=1, catalyst_id=item['catalyst_id'],
                evidence_revision='new-revision', purpose='entry', origin='direct_event', created_at=later(1)),
                event, expires_at=later(3600), db_path=self.db)
            return {'outcome': 'ready', 'reason': 'Enough', 'requests': []}
        self.assertEqual(self.run_research(planner=FakePlanner([supersede]))['status'], 'superseded')

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

    def test_boolean_company_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.tools.parse(self.request(company_id=True))

    def test_future_time_request_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cutoff'):
            self.tools.parse(self.request(as_of=later(1, at=self.tools.cutoff)))

    def test_arbitrary_operation_is_rejected(self):
        request = self.request()
        request['operation'] = 'submit_order'
        with self.assertRaisesRegex(ValueError, 'not approved'):
            self.tools.parse(request)

    def test_adapter_period_mismatch_is_rejected(self):
        _, ids = self.prior(period='Q2 FY2026')
        fact = get_company_fact(ids[0], db_path=self.db)
        doc = get_evidence_document(fact['document_id'], db_path=self.db)
        with self.assertRaisesRegex(ValueError, 'period mismatch'):
            self.tools.validate_result(self.tools.parse(self.request()),
                {'facts': [fact], 'documents': [doc], 'gaps': []})

    def test_numeric_claim_without_source_support_is_rejected(self):
        _, ids = self.prior()
        fact = get_company_fact(ids[0], db_path=self.db)
        fact.update(fact_id='fact:unsupported', value=999, range_high=1000)
        record_company_fact(CompanyFact(**fact), db_path=self.db)
        with self.assertRaisesRegex(ValueError, 'not present'):
            self.tools.execute(self.tools.parse(self.request()), timeout=10)

    def test_retrieval_reads_do_not_hold_a_transaction(self):
        self.prior()
        original = self.tools.validate_result
        def check(request, result):
            with closing(get_connection(self.db)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
            return original(request, result)
        with patch.object(self.tools, 'validate_result', side_effect=check):
            self.assertTrue(self.tools.execute(self.tools.parse(self.request()), timeout=10)['available'])

    def test_consensus_unavailable_is_explicit(self):
        result = self.tools.execute(self.tools.parse(self.request('get_available_consensus')), timeout=10)
        self.assertIn('requested_evidence_unavailable', result['gaps'])

    def test_prices_are_unavailable_without_recorded_response(self):
        result = self.tools.execute(self.tools.parse(self.request('get_event_price_response')), timeout=10)
        self.assertFalse(result['available'])

    def test_missing_requested_metric_is_not_reported_available(self):
        request = self.request('get_company_facts', metrics=['revenue', 'cash'])
        self.assertFalse(self.tools.execute(self.tools.parse(request), timeout=10)['available'])

    def test_event_documents_come_from_matching_archived_event(self):
        result = self.tools.execute(self.tools.parse(self.request('get_event_documents')), timeout=10)
        self.assertTrue(result['documents'] and result['available'])

    def test_unknown_event_cannot_access_other_event_documents(self):
        with self.assertRaisesRegex(ValueError, 'event mismatch'):
            self.tools.parse(self.request('get_event_documents', event_id='other-event'))

    def test_future_recorded_price_response_is_excluded(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('INSERT INTO catalyst_response_events VALUES (?,?,?,?,?,?,?,?)',
                (self.tools.event_id, 'NVDA', 'XLK', 'earnings_positive', 'positive', START, START, '{}'))
            conn.execute('INSERT INTO catalyst_responses VALUES (?,?,?,?,?,?,?)',
                (self.tools.event_id, 1, 'complete', START, later(1, at=self.tools.cutoff), .02, '{}'))
        self.assertFalse(self.tools.execute(self.tools.parse(self.request('get_event_price_response')), timeout=10)['available'])

    def test_recorded_price_response_includes_provenance_dates(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('INSERT INTO catalyst_response_events VALUES (?,?,?,?,?,?,?,?)',
                (self.tools.event_id, 'NVDA', 'XLK', 'earnings_positive', 'positive', START, START, '{}'))
            conn.execute('INSERT INTO catalyst_responses VALUES (?,?,?,?,?,?,?)',
                (self.tools.event_id, 1, 'complete', START, START, .02, '{}'))
        prices = self.tools.execute(self.tools.parse(self.request('get_event_price_response')), timeout=10)['price_responses']
        self.assertEqual((prices[0]['recorded_at'], prices[0]['residual_return']), (START, .02))

    def test_reordered_filters_share_request_identity(self):
        first = self.request('get_company_facts', metrics=['revenue', 'cash'])
        second = self.request('get_company_facts', metrics=['cash', 'revenue', 'cash'])
        self.assertEqual(self.tools.parse(first).key, self.tools.parse(second).key)


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

    def test_recorded_planner_response_is_reused_after_interruption(self):
        self.prior()
        original = ResearchOrchestrator.checkpoint
        def crash(worker, event, allowance=None):
            original(worker, event, allowance)
            if event['kind'] == 'planner_response':
                raise KeyboardInterrupt()
        with patch.object(ResearchOrchestrator, 'checkpoint', crash), self.assertRaises(KeyboardInterrupt):
            self.run_research()
        result = self.run_research()
        self.assertEqual((result['status'], len(self.planner.inputs)), ('ready', 1))

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

    def test_replay_never_calls_tools_or_model(self):
        result = self.run_research()
        with patch.object(ResearchTools, 'execute', side_effect=AssertionError('Network')), \
             patch.object(ResearchPlanner, 'plan', side_effect=AssertionError('Model')):
            self.assertEqual(self.trace(result)['session']['status'], result['status'])

    def test_resumed_session_retains_original_deadline(self):
        self.prior()
        original = ResearchOrchestrator.retrieve
        def crash(worker):
            original(worker)
            raise KeyboardInterrupt()
        with patch.object(ResearchOrchestrator, 'retrieve', crash), self.assertRaises(KeyboardInterrupt):
            self.run_research()
        self.assertEqual(self.run_research(clock=lambda: later(91))['status'], 'budget_exhausted')

    def test_resume_with_different_planner_version_does_not_dispatch(self):
        self.prior()
        original = ResearchOrchestrator.retrieve
        def crash(worker):
            original(worker)
            raise KeyboardInterrupt()
        with patch.object(ResearchOrchestrator, 'retrieve', crash), self.assertRaises(KeyboardInterrupt):
            self.run_research()
        other = FakePlanner()
        other.model = 'different-planner'
        self.assertEqual(self.run_research(planner=other)['stop_reason'], 'planner_version_changed_during_session')

    def test_daily_reservation_is_not_refunded_on_failure(self):
        self.planner.plan = Mock(side_effect=RuntimeError())
        self.run_research()
        with closing(get_connection(self.db)) as conn:
            calls = conn.execute("SELECT calls FROM research_daily_allowances WHERE kind='planner'").fetchone()[0]
        self.assertEqual(calls, 1)

    def test_queue_worker_consumes_shadow_candidates(self):
        result = process_research_queue(planner=self.planner, policy=self.research_policy,
                                        clock=lambda: ENTRY, db_path=self.db)
        self.assertEqual(len(result['sessions']), 1)

    def test_research_steps_are_immutable(self):
        self.run_research()
        import sqlite3
        with closing(get_connection(self.db)) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM research_steps')


class EmptyEvidenceTests(DiscoveryFixture):
    def test_news_only_candidate_gets_a_durable_missing_evidence_outcome(self):
        self.article()
        self.discover()
        candidate = self.process()['candidates'][0]
        result = ResearchOrchestrator(planner=FakePlanner(), policy=EvidenceResearchPolicy(mode='shadow'),
                                      clock=lambda: ENTRY, db_path=self.db).run(candidate)
        from agent_builders.company_evidence_payload import build_company_evidence_payload
        package = build_company_evidence_payload(1, package_id=result['final_package_id'], as_of=ENTRY, db_path=self.db)
        self.assertEqual((result['status'], package['facts']), ('deferred_missing_evidence', []))


class PlannerContractTests(unittest.TestCase):
    def test_planner_rejects_cloud_model(self):
        with self.assertRaises(ValueError):
            ResearchPlanner(model='fixture:cloud')

    def test_planner_rejects_credential_bearing_host(self):
        with self.assertRaises(ValueError):
            ResearchPlanner(model='fixture', host='http://secret:password@host')

    def test_terminal_plan_cannot_dispatch_operations(self):
        with self.assertRaises(ValueError):
            validate_plan({'outcome': 'ready', 'reason': 'Done', 'requests': [{}]})

    def test_transport_receives_hard_timeout_and_fixed_executable(self):
        import subprocess
        planner = ResearchPlanner(model='fixture')
        output = json.dumps({'text': json.dumps({'outcome': 'ready', 'reason': 'Done', 'requests': []}),
                             'usage': {}, 'diagnostics': {}})
        with patch('agent_stages.research_planner.subprocess.run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
            planner.plan({'company_id': 1}, timeout=7)
        self.assertEqual((run.call_args.kwargs['timeout'], len(run.call_args.args[0])), (7, 2))

    def test_integer_budgets_reject_booleans(self):
        with self.assertRaises(ValueError):
            EvidenceResearchPolicy(max_tool_calls=True)

    def test_live_mode_is_not_supported(self):
        with self.assertRaises(ValueError):
            EvidenceResearchPolicy(mode='live')


if __name__ == '__main__':
    unittest.main()
