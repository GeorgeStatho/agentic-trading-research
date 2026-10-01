"""Phase 7: forward cohorts, paired challenger evaluation, pre-registered acceptance, marks and ledgers."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agent_helpers.deterministic_option_selector as selector
from db_common import get_connection
from market_db_modules.candidate_lifecycle import record_candidate, record_candidate_snapshot
from market_db_modules.discovery_evaluation import discovery_route_ledger
from market_db_modules.forward_comparison import (
    cluster_bootstrap, cluster_labels, comparison_report, max_drawdown, promote_challenger,
)
from market_db_modules.forward_evaluation import (
    cohort_registrations, create_paired_candidate, paired_evaluation, reserve_challenger_calls,
)
from market_db_modules.foundation_store import canonical, digest
from market_db_modules.research_observations import get_research_observation, record_research_observation
from market_db_modules.research_outcomes import collect_research_thesis_outcomes, research_route_report
from market_db_modules.research_runs import collect_run, decision_quote, enrolls_candidate, start_run
from research_foundation_contracts import DECISION_OBSERVATION_PROTOCOL, ResearchObservation
from tests.research_foundation_fixtures import database
from services.challenger_evaluation import run_challenger_evaluations
from services.decision_observations import freeze_decision_observation, manager_input_payload
from services.evaluation_clock import evaluated_at
from services.forward_cohorts import ensure_comparison_cohort, ensure_production_cohort, paired_manifest
from services.forward_evaluation_policy import AcceptancePolicy, ChallengerPolicy, ForwardEvaluationPolicy
from services.liquidity_gate import evaluate_liquidity
from services.order_candidates import OrderCandidateBuilder
from services.paired_decisions import finalize_paired_decision
from services.research_contract import fingerprint
from services.research_marks import collect_research_marks
from services.research_promotion import champion_allows, research_only_candidate

CUTOFF = '2026-09-02T15:00:00+00:00'
EVALUATED = '2026-09-02T16:00:00+00:00'
REPORTED = '2026-09-10T20:00:00+00:00'
# The liquidity gate treats a quote more than five seconds old at decision time as stale.
QUOTED = '2026-09-02T14:59:58+00:00'
BASE = {'pipeline_version': 'v2:incumbent', 'model_provider': 'ollama',
        'model_names': {'strategist': 'strategist-a', 'manager': 'manager-a'},
        'process_settings': {'models': {'strategist': {'model': 'strategist-a'}, 'manager': {'model': 'manager-a'}}},
        'gate_config': {}}
CHALLENGER = ChallengerPolicy(mode='shadow', strategist_model='strategist-b', manager_model='manager-b')
SAME_STRATEGIST = ChallengerPolicy(mode='shadow', manager_model='manager-b')
ACCEPTANCE = AcceptancePolicy(window_days=30)
REGIME = {'state': 'trend_up', 'as_of': '2026-09-02T14:00:00+00:00'}
IDENTICAL = SimpleNamespace(mismatch=lambda manifest: None)
STRATEGIST_RECOMMENDATION = {'decision': 'trade_candidate', 'confidence': 'high', 'preferred_option_direction': 'call'}


def at(value):
    return lambda: value


def option(symbol, expiration, *, strike=102.0, delta=.28):
    return {'option_id': 1, 'symbol': symbol, 'contract_type': 'call', 'expiration_date': expiration,
            'strike_price': strike, 'open_interest': 250.0, 'implied_volatility': .45, 'iv_percentile': 45.0,
            'greeks': {'delta': delta, 'gamma': .02, 'theta': -.08, 'vega': .11},
            'latest_quote': {'bid_price': 1.95, 'ask_price': 2.0, 'midpoint_price': 1.975, 'bid_size': 50,
                             'ask_size': 50, 'timestamp': QUOTED}}


NEAR = option('NVDA260925C00102000', '2026-09-25')
FAR = option('NVDA261009C00103000', '2026-10-09', strike=103.0)
CHAINS = {'available': True, 'reference_stock_price': 100.0, 'buckets': {'20_30': [NEAR], '30_45': [FAR]}}
MARKET = {'market_regime': REGIME, 'current_stock_price': {'price': 100.0},
          'underlying_price_history': {'available': True, 'historical_volatility_20d': .35,
                                       'historical_volatility_60d': .32,
                                       'technical_summary': {'trend_state': 'bullish'},
                                       'technical_indicators': {'relative_volume': {'relative_volume_20d': 2.0}}},
          # Live snapshots write unknown quote times as "", as the index and sector quotes do here.
          'market_indices': {'sp500': {'price': 5000.0, 'timestamp': ''}}, 'sector_etf': {'timestamp': ''},
          'option_market': {'reference_stock_price': 100.0, 'contracts': [], 'selection_filters': {},
                            'volatility_summary': {'term_structure': {'event_risk_flag': False}}}}


def manager_recommendation(bucket, decision='call'):
    return {'decision': decision, 'confidence': 'high', 'thesis_confidence': 'high', 'target_dte_bucket': bucket,
            'reason': 'fixture'}


def production_decision(db, *, bucket='20_30', created_at='2026-09-02T14:50:00+00:00', article_time=None):
    """A production candidate with the strategist and manager inputs production checkpoints."""
    with patch('market_db_modules.candidate_lifecycle.now_iso', return_value=created_at):
        context = record_candidate('NVDA', BASE, evidence=[], db_path=db)
    company = {'company_id': 1, 'symbol': 'NVDA', 'name': 'Fixture'}
    evidence = {'grounding': {'article_ids': [7]}, 'opportunist_rollup': {'company': {'dominant_direction': 'positive'}},
                'supporting_articles': {'company': [{'article_id': 7, 'published_at': article_time or created_at}]}}
    strategist_input = {'company': company, **evidence, 'market_context': {'market_regime': REGIME},
                        'candidate_id': context['candidate_id']}
    manager_payload = {'company': company, **evidence, 'market_context': MARKET, 'candidate_id': context['candidate_id'],
                       'strategist_recommendation': STRATEGIST_RECOMMENDATION}
    for stage, payload in (('pre_strategist', strategist_input),
                           ('post_strategist', {'recommendation': STRATEGIST_RECOMMENDATION}),
                           ('pre_manager', {'payload': manager_payload, 'model': 'manager-a'}),
                           ('pre_gates', {'payload': manager_payload, 'recommendation': manager_recommendation(bucket),
                                          'market_context': MARKET})):
        with patch('market_db_modules.candidate_lifecycle.now_iso', return_value=created_at):
            record_candidate_snapshot(context, stage, payload, db_path=db)
    return context


def fake_strategist(payload, *, model, thesis_policy):
    return {'recommendation': {**STRATEGIST_RECOMMENDATION, 'confidence': 'medium'}, 'thesis_check': None,
            'prompt_version': 'fixture', 'model': model}


def fake_manager(bucket='30_45'):
    calls = []

    def manager(payload, *, model):
        calls.append({'model': model, 'strategist': payload['strategist_recommendation']})
        return {'recommendation': manager_recommendation(bucket)}
    manager.calls = calls
    return manager


def market_data(db):
    """Closing marks: the 20-30 DTE contract falls through its stop; the 30-45 DTE one hits take-profit."""
    with closing(get_connection(db)) as conn, conn:
        for day, near_bid, far_bid in ((3, 1.3, 3.1), (4, 1.2, 3.2), (8, 1.2, 3.2), (9, 1.2, 3.2)):
            marked = f'2026-09-{day:02d}T19:50:00+00:00'
            for symbol, bid in ((NEAR['symbol'], near_bid), (FAR['symbol'], far_bid)):
                conn.execute('''INSERT INTO historical_option_quotes (option_symbol,quote_at,bid_price,ask_price,provider,raw_json)
                    VALUES (?,?,?,?,'alpaca',?)''', (symbol, marked, bid, bid + .1, canonical({'available_at': marked})))
        for symbol in ('SPY', 'SMH', 'QQQ'):
            for day in (2, 3, 4, 8, 9):
                closed = f'2026-09-{day:02d}T20:00:00+00:00'
                conn.execute('''INSERT INTO historical_underlying_bars (symbol,bar_at,timeframe,close_price,provider,raw_json,created_at)
                    VALUES (?,?,'1d',100,'fixture',?,?)''', (symbol, closed, canonical({'available_at': closed}), closed))


class ForwardTestCase(unittest.TestCase):
    def setUp(self):
        self.db = database(self)

    def freeze(self, context, policy=CHALLENGER, chains=CHAINS, clock=CUTOFF):
        return freeze_decision_observation(context, policy=policy, acceptance=ACCEPTANCE, fetch_chains=lambda *a, **k: chains,
                                           per_type_limit=6, clock=at(clock), db_path=self.db)

    def evaluate(self, policy=CHALLENGER, manager=None, strategist=fake_strategist, identity=IDENTICAL, clock=EVALUATED):
        with patch('market_db_modules.candidate_lifecycle.now_iso', return_value=clock), \
                patch.object(selector, 'OPTION_SELECTOR_MODE', 'simple'):
            return run_challenger_evaluations(policy=policy, strategist=strategist, manager=manager or fake_manager(),
                                              persist_check=lambda *a, **k: None, identity=identity, clock=at(clock),
                                              db_path=self.db)

    def cohort(self):
        with closing(get_connection(self.db)) as conn:
            return conn.execute('SELECT cohort_id FROM research_run_acceptance LIMIT 1').fetchone()['cohort_id']


class EndToEndPairingTests(ForwardTestCase):
    """Acceptance: a genuinely different strategy is paired with the incumbent and evaluated."""

    def setUp(self):
        super().setUp()
        self.production = production_decision(self.db)
        self.observation_id = self.freeze(self.production)
        self.manager = fake_manager()
        self.result = self.evaluate(manager=self.manager)
        market_data(self.db)
        registrations = cohort_registrations(self.cohort(), db_path=self.db)
        self.runs = {role: registration['run_id'] for role, registration in registrations.items()}
        for run_id in self.runs.values():
            collect_run(run_id, as_of=REPORTED, db_path=self.db)
        self.report = comparison_report(self.cohort(), as_of=REPORTED, db_path=self.db)

    def test_challenger_and_incumbent_pair_on_the_same_observation(self):
        self.assertEqual((self.report['n_paired'], self.report['observations']['recorded']), (1, 1))

    def test_challenger_picked_its_own_bucket_from_the_frozen_chains(self):
        selections = {}
        for role, run_id in self.runs.items():
            with closing(get_connection(self.db)) as conn:
                inputs = json.loads(conn.execute('SELECT input_json FROM research_candidates WHERE run_id=?',
                                                 (run_id,)).fetchone()['input_json'])
            selections[role] = inputs['selected_contract']['symbol']
        self.assertEqual(selections, {'incumbent': NEAR['symbol'], 'challenger': FAR['symbol']})

    def test_paired_difference_uses_decision_quote_entry_and_closing_marks(self):
        # Challenger: ask 2.00 -> take-profit bid 3.10; incumbent: ask 2.00 -> stop at bid 1.30; $0.65 fees per side.
        self.assertAlmostEqual(self.report['paired_difference'], 108.7 - (-71.3))

    def test_challenger_models_were_called_and_incumbent_outputs_were_not_replayed(self):
        evaluations = {role: paired_evaluation(self.observation_id, role, db_path=self.db) for role in self.runs}
        self.assertEqual({role: row['model_calls'] for role, row in evaluations.items()}, {'incumbent': 0, 'challenger': 2})

    def test_challenger_manager_saw_the_challenger_strategist(self):
        self.assertEqual(self.manager.calls[0]['strategist'].get('confidence'), 'medium')

    def test_report_is_interim_before_the_window_closes(self):
        self.assertEqual(self.report['verdict'], 'interim')

    def test_production_candidate_is_not_bound_to_the_observation(self):
        with closing(get_connection(self.db)) as conn:
            bound = conn.execute('SELECT 1 FROM candidate_observations WHERE candidate_id=?',
                                 (self.production['candidate_id'],)).fetchone()
        self.assertIsNone(bound)

    def test_paired_snapshots_never_hold_information_after_the_cutoff(self):
        with closing(get_connection(self.db)) as conn:
            cutoffs = {row[0] for row in conn.execute(
                "SELECT DISTINCT information_cutoff_time FROM candidate_snapshots WHERE candidate_id LIKE 'paired:%'")}
        self.assertEqual(cutoffs, {CUTOFF})


class ObservationTests(ForwardTestCase):
    def test_observation_holds_inputs_but_not_strategy_outputs(self):
        source = get_research_observation(self.freeze(production_decision(self.db)), db_path=self.db)['source_snapshot']
        self.assertNotIn('strategist_recommendation', manager_input_payload(source))

    def test_shared_evidence_is_stored_once_and_rebuilt_for_the_manager(self):
        source = get_research_observation(self.freeze(production_decision(self.db)), db_path=self.db)['source_snapshot']
        self.assertEqual(manager_input_payload(source)['grounding'], source['strategist_input']['grounding'])

    def test_decision_observations_use_their_own_protocol(self):
        observation = get_research_observation(self.freeze(production_decision(self.db)), db_path=self.db)
        self.assertEqual(observation['protocol_version'], DECISION_OBSERVATION_PROTOCOL)

    def test_future_evidence_cannot_enter_an_earlier_observation(self):
        context = production_decision(self.db, article_time='2026-09-03T12:00:00+00:00')
        self.assertIsNone(self.freeze(context))

    def test_unavailable_observation_is_recorded_with_its_reason(self):
        context = production_decision(self.db, article_time='2026-09-03T12:00:00+00:00')
        self.freeze(context)
        with closing(get_connection(self.db)) as conn:
            row = conn.execute('SELECT status, reason FROM decision_observations WHERE candidate_id=?',
                               (context['candidate_id'],)).fetchone()
        self.assertEqual(row['status'], 'unavailable')

    def test_disabled_challenger_records_nothing(self):
        self.assertIsNone(self.freeze(production_decision(self.db), policy=ChallengerPolicy()))

    def test_missing_decision_inputs_never_raise_into_the_cycle(self):
        with patch('market_db_modules.candidate_lifecycle.now_iso', return_value=CUTOFF):
            context = record_candidate('NVDA', BASE, evidence=[], db_path=self.db)
        self.assertIsNone(self.freeze(context))


class FinalizationTests(ForwardTestCase):
    def test_future_timestamp_in_a_paired_decision_is_rejected(self):
        observation = get_research_observation(self.freeze(production_decision(self.db)), db_path=self.db)
        run_id = cohort_registrations(self.cohort(), db_path=self.db)['challenger']['run_id']
        context = {**create_paired_candidate(observation, role='challenger', run_id=run_id, manifest=BASE,
                                             at=EVALUATED, db_path=self.db), 'db_path': self.db}
        payload = manager_input_payload(observation['source_snapshot'])
        payload['strategist_recommendation'] = {**STRATEGIST_RECOMMENDATION, 'observed_at': '2026-09-03T00:00:00+00:00'}
        with patch('market_db_modules.candidate_lifecycle.now_iso', return_value=EVALUATED), self.assertRaises(ValueError):
            finalize_paired_decision(context, observation, payload, manager_recommendation('none', 'neither'),
                                     strategist_recommendation=STRATEGIST_RECOMMENDATION, model='m',
                                     thesis_policy=SimpleNamespace(mode='off'),
                                     economics_policy=SimpleNamespace(mode='shadow'))

    def test_quote_age_is_measured_at_the_evaluation_cutoff(self):
        with evaluated_at(CUTOFF):
            verdict = evaluate_liquidity(FAR)
        self.assertNotIn('liquidity_stale_quote', verdict['reason_codes'])


class ChallengerRunnerTests(ForwardTestCase):
    def setUp(self):
        super().setUp()
        self.observation_id = self.freeze(production_decision(self.db))

    def test_call_cap_defers_the_backlog_instead_of_dropping_it(self):
        later = self.freeze(production_decision(self.db, created_at='2026-09-02T14:55:00+00:00'),
                            clock='2026-09-02T15:05:00+00:00')
        result = self.evaluate(policy=replace(CHALLENGER, daily_model_calls=2))
        statuses = [(paired_evaluation(identity, 'challenger', db_path=self.db) or {}).get('status')
                    for identity in (self.observation_id, later)]
        self.assertEqual((result['stopped'], statuses), ('daily_call_limit', ['evaluated', None]))

    def test_exhausted_allowance_stops_before_any_model_call(self):
        reserve_challenger_calls(2, limit=2, at=EVALUATED, db_path=self.db)
        manager = fake_manager()
        result = self.evaluate(policy=replace(CHALLENGER, daily_model_calls=2), manager=manager)
        self.assertEqual((result['stopped'], manager.calls), ('daily_call_limit', []))

    def test_model_failure_retries_then_becomes_unavailable_not_cash(self):
        def broken(payload, *, model):
            raise RuntimeError('provider down')
        self.evaluate(manager=broken)
        first = paired_evaluation(self.observation_id, 'challenger', db_path=self.db)['status']
        self.evaluate(manager=broken)
        self.assertEqual((first, paired_evaluation(self.observation_id, 'challenger', db_path=self.db)['status']),
                         ('pending', 'unavailable'))

    def test_unavailable_incumbent_spends_no_challenger_calls(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("DELETE FROM candidate_snapshots WHERE stage='pre_gates' AND candidate_id NOT LIKE 'paired:%'")
        manager = fake_manager()
        self.evaluate(manager=manager)
        row = paired_evaluation(self.observation_id, 'challenger', db_path=self.db)
        self.assertEqual((row['reason'], manager.calls), ('incumbent_unavailable', []))

    def test_changed_implementation_is_unavailable_without_model_calls(self):
        manager = fake_manager()
        self.evaluate(manager=manager, identity=SimpleNamespace(mismatch=lambda manifest: 'implementation_changed'))
        row = paired_evaluation(self.observation_id, 'incumbent', db_path=self.db)
        self.assertEqual((row['reason'], manager.calls), ('implementation_changed', []))

    def test_unchanged_strategist_reuses_the_incumbent_output(self):
        observation_id = self.freeze(production_decision(self.db, created_at='2026-09-02T14:52:00+00:00'),
                                     policy=SAME_STRATEGIST)
        strategist_calls = []
        self.evaluate(policy=SAME_STRATEGIST, strategist=lambda *a, **k: strategist_calls.append(1))
        self.assertEqual((strategist_calls, paired_evaluation(observation_id, 'challenger', db_path=self.db)['model_calls']),
                         ([], 1))

    def test_earlier_challenger_cohorts_are_not_run_with_the_new_configuration(self):
        manager = fake_manager()
        self.evaluate(policy=SAME_STRATEGIST, manager=manager)
        self.assertEqual((manager.calls, paired_evaluation(self.observation_id, 'challenger', db_path=self.db)), ([], None))


class CohortTests(ForwardTestCase):
    def test_production_cohort_opens_once_per_manifest(self):
        first = ensure_production_cohort(BASE, acceptance=ACCEPTANCE, at=CUTOFF, db_path=self.db)
        again = ensure_production_cohort(BASE, acceptance=ACCEPTANCE, at=EVALUATED, db_path=self.db)
        self.assertEqual(first['cohort_id'], again['cohort_id'])

    def test_changed_manifest_opens_a_new_cohort(self):
        first = ensure_production_cohort(BASE, acceptance=ACCEPTANCE, at=CUTOFF, db_path=self.db)
        changed = ensure_production_cohort({**BASE, 'pipeline_version': 'v2:other'}, acceptance=ACCEPTANCE,
                                           at=EVALUATED, db_path=self.db)
        self.assertNotEqual(first['cohort_id'], changed['cohort_id'])

    def test_window_end_rolls_into_a_new_cohort(self):
        first = ensure_production_cohort(BASE, acceptance=ACCEPTANCE, at=CUTOFF, db_path=self.db)
        later = ensure_production_cohort(BASE, acceptance=ACCEPTANCE, at='2026-10-03T15:00:00+00:00', db_path=self.db)
        self.assertNotEqual(first['cohort_id'], later['cohort_id'])

    def test_disabled_forward_evaluation_opens_nothing(self):
        self.assertIsNone(ensure_production_cohort(BASE, policy=ForwardEvaluationPolicy(mode='off'), db_path=self.db))

    def test_acceptance_is_frozen_when_the_cohort_opens(self):
        cohort = ensure_comparison_cohort(BASE, CHALLENGER, acceptance=ACCEPTANCE, at=CUTOFF, db_path=self.db)
        with patch.dict(os.environ, {'FORWARD_MINIMUM_CLUSTERS': '99'}):
            registration = cohort_registrations(cohort['cohort_id'], db_path=self.db)['challenger']
        self.assertEqual(registration['policy']['minimum_clusters'], ACCEPTANCE.minimum_clusters)

    def test_pre_registration_cannot_be_rewritten(self):
        cohort = ensure_comparison_cohort(BASE, CHALLENGER, acceptance=ACCEPTANCE, at=CUTOFF, db_path=self.db)
        with closing(get_connection(self.db)) as conn, self.assertRaises(Exception):
            conn.execute("UPDATE research_run_acceptance SET policy_json='{}' WHERE cohort_id=?", (cohort['cohort_id'],))

    def test_misconfigured_research_settings_never_stop_the_manifest(self):
        from services.pipeline_version import build_pipeline_version
        with patch.dict(os.environ, {'RESEARCH_CHALLENGER_MODE': 'shadow', 'FORWARD_MINIMUM_PAIRED_OBSERVATIONS': '5',
                                     'EVENT_THESIS_REVIEW_MODE': 'live'}):
            manifest = build_pipeline_version()
        self.assertIn('configuration_error', manifest['forward_evaluation']['challenger'])

    def test_acceptance_cannot_lower_existing_floors(self):
        for fields in ({'minimum_paired_observations': 99}, {'minimum_regimes': 1}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                AcceptancePolicy(**fields)

    def test_challenger_manifest_swaps_models_and_is_distinct(self):
        manifest = paired_manifest(BASE, role='challenger', challenger=CHALLENGER)
        self.assertEqual((manifest['model_names'], manifest['pipeline_version'] != BASE['pipeline_version']),
                         ({'strategist': 'strategist-b', 'manager': 'manager-b'}, True))

    def test_incumbent_evaluation_manifest_differs_from_production(self):
        self.assertNotEqual(paired_manifest(BASE, role='incumbent')['pipeline_version'], BASE['pipeline_version'])


class RouteIsolationTests(ForwardTestCase):
    """Acceptance: a research candidate never reaches broker execution."""

    def test_production_cohorts_exclude_research_only_candidates(self):
        self.assertEqual([enrolls_candidate('production', identity) for identity in ('uuid', 'discovery:x', 'paired:x')],
                         [True, False, False])

    def test_paired_cohorts_enroll_only_paired_evaluations(self):
        self.assertEqual([enrolls_candidate('challenger', identity) for identity in ('uuid', 'paired:challenger:x')],
                         [False, True])

    def test_discovery_candidate_does_not_enter_a_production_run(self):
        run = start_run(BASE, target_candidates=10, db_path=self.db, now='2026-09-01T00:00:00+00:00')
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO candidates (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,
                pipeline_version_json) VALUES ('discovery:x','NVDA',?,'[]','[]',?)''', (CUTOFF, canonical(BASE)))
        self.assertEqual(collect_run(run['run_id'], as_of=REPORTED, db_path=self.db)['candidates'], 0)

    def test_order_builder_rejects_paired_candidates(self):
        builder = OrderCandidateBuilder.__new__(OrderCandidateBuilder)
        self.assertFalse(builder._is_trade_eligible({'candidate_id': 'paired:challenger:x', 'decision': 'call'}))

    def test_champion_guard_rejects_paired_candidates(self):
        self.assertFalse(champion_allows({'candidate_id': 'paired:incumbent:x'}, db_path=self.db))

    def test_research_only_prefixes(self):
        self.assertTrue(research_only_candidate('paired:incumbent:x') and research_only_candidate('discovery:y'))


class DecisionQuoteTests(unittest.TestCase):
    def test_decision_quote_is_an_entry_available_at_the_cutoff(self):
        self.assertEqual(decision_quote(FAR, CUTOFF)['available_at'], CUTOFF)

    def test_quote_after_the_cutoff_is_not_an_entry(self):
        self.assertIsNone(decision_quote(FAR, '2026-09-02T14:00:00+00:00'))


class StatisticsTests(unittest.TestCase):
    def pair(self, day, symbol='NVDA', catalyst_id='catalyst:a'):
        return {'cutoff': f'2026-09-{day:02d}T15:00:00+00:00', 'symbol': symbol, 'catalyst_id': catalyst_id}

    def test_repeated_decisions_on_one_announcement_share_a_cluster(self):
        labels = cluster_labels([self.pair(2), self.pair(3), self.pair(9)])
        self.assertEqual(len(set(labels)), 1)

    def test_same_company_on_the_same_day_shares_a_cluster(self):
        labels = cluster_labels([self.pair(2, catalyst_id='catalyst:a'), self.pair(2, catalyst_id='catalyst:b')])
        self.assertEqual(len(set(labels)), 1)

    def test_unrelated_events_on_one_day_are_separate_clusters(self):
        labels = cluster_labels([self.pair(2, 'NVDA', 'catalyst:a'), self.pair(2, 'AMD', 'catalyst:b')])
        self.assertEqual(len(set(labels)), 2)

    def test_placeholder_catalysts_link_nothing(self):
        labels = cluster_labels([self.pair(2, 'NVDA', 'catalyst:unknown:x'), self.pair(3, 'NVDA', 'catalyst:unknown:x')])
        self.assertEqual(len(set(labels)), 2)

    def test_bootstrap_is_reproducible(self):
        run = lambda: cluster_bootstrap([1, -2, 3, 4], [0, 1, 2, 3], resamples=500, confidence=.9, seed=7)
        self.assertEqual(run(), run())

    def test_bootstrap_resamples_whole_clusters(self):
        result = cluster_bootstrap([5, -100, -100], [0, 1, 1], resamples=500, confidence=.9, seed=3)
        self.assertLess(result['lower_bound'], 0)

    def test_drawdown_orders_trades_by_exit(self):
        trades = [{'status': 'complete', 'hypothetical_pnl': value, 'hypothetical_exit': {'at': f'2026-09-0{day}'}}
                  for day, value in ((1, 10), (2, -30), (3, 5))]
        self.assertEqual(max_drawdown(trades), 30)


def synthetic_cohort(db, *, observations=105, challenger=40.0, incumbent=-10.0, missing=(), as_of=None):
    """A sealed paired cohort written straight into the research tables, without the decision pipeline."""
    start = datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    cohort = ensure_comparison_cohort(BASE, CHALLENGER, acceptance=SYNTHETIC_ACCEPTANCE, at=start.isoformat(), db_path=db)
    with closing(get_connection(db)) as conn, conn:
        runs = {role: json.loads(conn.execute('SELECT manifest_json FROM research_runs WHERE run_id=?',
                                              (run_id,)).fetchone()[0]) for role, run_id in cohort['runs'].items()}
    for index in range(observations):
        day = index // 3
        cutoff = (start + timedelta(days=day, hours=1 + index % 3)).isoformat()
        # Three decisions a day on one announcement: 35 days give 35 independent clusters.
        event = catalyst(db, f'catalyst:synthetic:{day}')['catalyst_id']
        observation_id = record_research_observation(ResearchObservation(
            company_id=1, symbol='NVDA', catalyst_id=event, event_revision=f'r{index}', information_cutoff_time=cutoff,
            protocol_version=DECISION_OBSERVATION_PROTOCOL, source_snapshot={'decision_route': 'production', 'index': index}),
            db_path=db)
        with closing(get_connection(db)) as conn, conn:
            production = f'production-{index}'
            conn.execute('''INSERT INTO candidates (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,
                pipeline_version_json) VALUES (?,?,?,'[]','[]',?)''', (production, 'NVDA', cutoff, canonical(BASE)))
            conn.execute("INSERT INTO decision_observations VALUES (?,?,?,'recorded',NULL,?)",
                         (production, observation_id, cohort['cohort_id'], cutoff))
            for role, pnl in (('incumbent', incumbent), ('challenger', challenger)):
                run_id, candidate_id = cohort['runs'][role], f'paired:{role}:{index}'
                conn.execute('''INSERT INTO candidates (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,
                    pipeline_version_json) VALUES (?,?,?,'[]','[]',?)''', (candidate_id, 'NVDA', cutoff, canonical(runs[role])))
                features = {'regime': 'trend_up' if day % 2 else 'range', 'manager_direction': 'call'}
                inputs = {'candidate_id': candidate_id, 'symbol': f'T{day % 7}', 'created_at': cutoff,
                          'information_cutoff_time': cutoff,
                          'features': features, 'selected_contract': None, 'observation_id': observation_id}
                conn.execute('INSERT INTO research_candidates VALUES (?,?,?,?,?)',
                             (run_id, candidate_id, cutoff, fingerprint(inputs), canonical(inputs)))
                conn.execute('INSERT INTO strategy_evaluations VALUES (?,?,?,?,?,?,?,?,?)',
                             ('evaluation:' + digest([run_id, observation_id]), run_id, observation_id, candidate_id,
                              'full_v2', digest(runs[role]), digest(inputs),
                              digest({'features': features, 'selected_contract': None}), 'evaluated'))
                outcomes = {'full_v2': pnl, 'sentiment_volume': -20.0, 'sentiment_trend': -15.0,
                            'taxonomy_expectancy': -5.0, 'SMH': 1.0, 'QQQ': 2.0}
                for strategy, value in outcomes.items():
                    exit_at = (datetime.fromisoformat(cutoff) + timedelta(days=5)).isoformat()
                    status = 'missing_entry_quote' if role == 'challenger' and index in missing and strategy == 'full_v2' else 'complete'
                    result = {'status': status, 'hypothetical_pnl': value if status == 'complete' else None,
                              'capital': 200.65, 'verified_daily_marks': True, 'hypothetical_exit': {'at': exit_at}}
                    conn.execute('INSERT INTO research_shadow_positions VALUES (?,?,?,?,?,?,?)',
                                 (run_id, candidate_id, strategy, cutoff, cutoff, status, canonical(result)))
                conn.execute("INSERT INTO paired_evaluations VALUES (?,?,?,?,?,'evaluated',NULL,1,?,1.5,?)",
                             (observation_id, role, cohort['cohort_id'], run_id, candidate_id,
                              2 if role == 'challenger' else 0, cutoff))
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE research_runs SET status='sealed' WHERE run_id IN (?,?)", tuple(cohort['runs'].values()))
    return cohort


SYNTHETIC_ACCEPTANCE = AcceptancePolicy(window_days=40)
# The 40-day window ends 2026-10-11; reports become final two days later.
AFTER_WINDOW = '2026-10-14T00:00:00+00:00'


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)

    def test_superior_challenger_meets_pre_registered_criteria(self):
        cohort = synthetic_cohort(self.db)
        self.assertEqual(comparison_report(cohort['cohort_id'], as_of=AFTER_WINDOW, db_path=self.db)['verdict'], 'accepted')

    def test_accepted_challenger_can_be_approved_once(self):
        cohort = synthetic_cohort(self.db)
        report = comparison_report(cohort['cohort_id'], as_of=AFTER_WINDOW, db_path=self.db)
        approval = promote_challenger(report['report_id'], db_path=self.db)
        self.assertEqual(approval['challenger']['challenger_id'], CHALLENGER.challenger_id)

    def test_worse_challenger_is_not_accepted(self):
        cohort = synthetic_cohort(self.db, challenger=-30.0, incumbent=10.0)
        self.assertEqual(comparison_report(cohort['cohort_id'], as_of=AFTER_WINDOW, db_path=self.db)['verdict'],
                         'not_accepted')

    def test_report_before_the_window_closes_is_interim_and_cannot_be_approved(self):
        cohort = synthetic_cohort(self.db)
        report = comparison_report(cohort['cohort_id'], as_of='2026-09-20T00:00:00+00:00', db_path=self.db)
        with self.assertRaises(ValueError):
            promote_challenger(report['report_id'], db_path=self.db)

    def test_missing_quotes_cannot_count_as_avoided_losses(self):
        cohort = synthetic_cohort(self.db, challenger=40.0, incumbent=-10.0, missing=range(0, 105, 2))
        report = comparison_report(cohort['cohort_id'], as_of=AFTER_WINDOW, db_path=self.db)
        self.assertAlmostEqual(report['paired_difference'], 52 * 50.0)

    def test_missing_outcomes_lower_coverage_and_fail_acceptance(self):
        cohort = synthetic_cohort(self.db, missing=range(0, 105, 2))
        report = comparison_report(cohort['cohort_id'], as_of=AFTER_WINDOW, db_path=self.db)
        coverage = next(row for row in report['criteria'] if row['criterion'] == 'coverage')
        self.assertEqual((coverage['passed'], report['verdict']), (False, 'not_accepted'))

    def test_changed_implementation_refuses_approval(self):
        cohort = synthetic_cohort(self.db)
        report = comparison_report(cohort['cohort_id'], as_of=AFTER_WINDOW, db_path=self.db)
        with patch('market_db_modules.forward_comparison.source_hash', return_value='changed'), \
                self.assertRaises(ValueError):
            promote_challenger(report['report_id'], db_path=self.db)

    def test_criteria_come_from_the_registration_not_the_environment(self):
        cohort = synthetic_cohort(self.db)
        with patch.dict(os.environ, {'FORWARD_MINIMUM_CLUSTERS': '999'}):
            report = comparison_report(cohort['cohort_id'], as_of=AFTER_WINDOW, db_path=self.db)
        self.assertEqual(report['acceptance']['policy']['minimum_clusters'], SYNTHETIC_ACCEPTANCE.minimum_clusters)


class MarkTests(ForwardTestCase):
    CLOSE = '2026-09-03T20:00:00+00:00'

    def pending_position(self):
        run = start_run(BASE, target_candidates=10, db_path=self.db, now='2026-09-01T00:00:00+00:00')
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO candidates (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,
                pipeline_version_json) VALUES ('c1','NVDA',?,'[]','[]',?)''', (CUTOFF, canonical(BASE)))
            conn.execute("INSERT INTO research_candidates VALUES (?, 'c1', ?, 'h', '{}')", (run['run_id'], CUTOFF))
            conn.execute("INSERT INTO research_shadow_positions VALUES (?, 'c1', 'full_v2', ?, ?, 'pending', ?)",
                         (run['run_id'], CUTOFF, CUTOFF, canonical({'option_symbol': FAR['symbol']})))

    def collect(self, now, fetch=None):
        with patch('services.research_marks.collect_active_runs'):
            return collect_research_marks(at=now, close_at=self.CLOSE, db_path=self.db, policy=ForwardEvaluationPolicy(),
                                          fetch_quotes=fetch or (lambda symbols: {symbol: {
                                              'bid_price': 2.5, 'ask_price': 2.6, 'timestamp': '2026-09-03T19:52:00+00:00'}
                                              for symbol in symbols}))

    def test_marks_are_not_polled_outside_the_closing_window(self):
        self.assertEqual(self.collect('2026-09-03T18:00:00+00:00')['status'], 'not_due')

    def test_open_contracts_are_marked_once_per_session(self):
        self.pending_position()
        first = self.collect('2026-09-03T19:52:00+00:00')
        second = self.collect('2026-09-03T19:55:00+00:00')
        self.assertEqual((first['stored'], second['status']), (1, 'already_ran'))

    def test_marks_share_the_decision_quote_provider(self):
        self.pending_position()
        self.collect('2026-09-03T19:52:00+00:00')
        with closing(get_connection(self.db)) as conn:
            provider = conn.execute('SELECT provider FROM historical_option_quotes').fetchone()['provider']
        self.assertEqual(provider, 'alpaca')


def catalyst(db, catalyst_id, *, direction='positive', response=None, published='2026-08-31T12:00:00+00:00'):
    from news_db_modules.catalysts import build_catalyst_state, store_catalyst_state
    with closing(get_connection(db)) as conn, conn:
        state = build_catalyst_state(catalyst_id, [], ticker='NVDA')
        state.update(catalyst_family='earnings_positive', direction=direction, first_public_time=published)
        store_catalyst_state(state, conn=conn)
        if response is not None:
            conn.execute('INSERT INTO catalyst_response_events VALUES (?,?,?,?,?,?,?,?)',
                         (catalyst_id, 'NVDA', None, 'earnings_positive', direction, CUTOFF, CUTOFF, canonical(state)))
            conn.execute("INSERT INTO catalyst_responses VALUES (?,5,'complete',?,?,?,'{}')",
                         (catalyst_id, REPORTED, REPORTED, response))
    return state


class DiscoveryLedgerTests(ForwardTestCase):
    def production(self, catalyst_id, *, admitted):
        with closing(get_connection(self.db)) as conn, conn:
            identity = 'prod-' + catalyst_id
            conn.execute('''INSERT INTO candidates (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,
                pipeline_version_json) VALUES (?,?,?,'[]','[]',?)''', (identity, 'NVDA', CUTOFF, canonical(BASE)))
            conn.execute('INSERT INTO candidate_catalysts VALUES (?,?,?,1,?)', (identity, catalyst_id, 'earnings_positive', '{}'))
        context = {'candidate_id': identity, 'pipeline_version': BASE, 'db_path': self.db, 'symbol': 'NVDA'}
        if admitted:
            record_candidate_snapshot(context, 'pre_strategist', {}, cutoff=CUTOFF)
        else:
            from market_db_modules.candidate_lifecycle import record_candidate_rejection
            from services.candidate_lifecycle import GateResult
            record_candidate_rejection(context, GateResult(False, 'SUPPORT', 'insufficient_high_confidence_articles', {}, {}))

    def ledger(self):
        return discovery_route_ledger(days=30, as_of=REPORTED, db_path=self.db)

    def test_admitted_events_are_grouped_by_admitting_route(self):
        catalyst(self.db, 'catalyst:a', response=.04)
        self.production('catalyst:a', admitted=True)
        self.assertEqual(self.ledger()['outcomes_by_admission']['production_only']['5d']['resolved'], 1)

    def test_deliberate_non_admission_keeps_its_reason(self):
        catalyst(self.db, 'catalyst:b')
        self.production('catalyst:b', admitted=False)
        reasons = self.ledger()['non_admission_reasons']['production']
        self.assertIn('not_admitted:insufficient_high_confidence_articles', reasons)

    def test_route_that_never_saw_the_event_is_not_evaluated(self):
        catalyst(self.db, 'catalyst:c')
        self.production('catalyst:c', admitted=True)
        self.assertEqual(self.ledger()['route_status_counts']['discovery'], {'not_evaluated': 1})

    def test_bearish_event_outcome_is_signed_by_direction(self):
        catalyst(self.db, 'catalyst:d', direction='negative', response=-.05)
        self.production('catalyst:d', admitted=True)
        outcome = self.ledger()['outcomes_by_admission']['production_only']['5d']
        self.assertAlmostEqual(outcome['mean_signed_residual_return'], .05)

    def test_unregistered_event_stays_unresolved_not_zero(self):
        catalyst(self.db, 'catalyst:e')
        self.production('catalyst:e', admitted=True)
        outcome = self.ledger()['outcomes_by_admission']['production_only']['5d']
        self.assertEqual((outcome['resolved'], outcome['mean_signed_residual_return']), (0, None))


class ResearchOutcomeTests(ForwardTestCase):
    def thesis(self, direction='down', maximum_days=7):
        from market_db_modules.evidence_thesis import record_evidence_thesis
        return record_evidence_thesis({'route': 'research_shadow', 'company_id': 1, 'status': 'validated',
                                       'thesis': {'direction': direction, 'horizon': {'minimum_days': 1,
                                                                                      'maximum_days': maximum_days}},
                                       'created_at': CUTOFF}, db_path=self.db)

    def collect(self, response):
        with patch('market_db_modules.research_outcomes.measure_response', return_value=response) as measured:
            collect_research_thesis_outcomes(as_of=REPORTED, db_path=self.db)
        return measured

    def test_outcome_is_measured_from_thesis_time_over_the_snapped_horizon(self):
        self.thesis(maximum_days=7)
        measured = self.collect({'status': 'complete', 'residual_return': -.02, 'outcome_at': REPORTED})
        self.assertEqual((measured.call_args.kwargs['event_at'], measured.call_args.kwargs['horizon_days']), (CUTOFF, 5))

    def test_outcome_is_signed_by_thesis_direction(self):
        self.thesis(direction='down')
        self.collect({'status': 'complete', 'residual_return': -.02, 'outcome_at': REPORTED})
        summary = research_route_report(days=0, as_of=REPORTED, db_path=self.db)['outcomes_by_check_status']
        self.assertAlmostEqual(summary['validated']['mean_signed_residual_return'], .02)

    def test_complete_outcome_is_never_rewritten(self):
        self.thesis()
        self.collect({'status': 'complete', 'residual_return': -.02, 'outcome_at': REPORTED})
        self.collect({'status': 'complete', 'residual_return': .5, 'outcome_at': REPORTED})
        summary = research_route_report(days=0, as_of=REPORTED, db_path=self.db)['outcomes_by_check_status']
        self.assertAlmostEqual(summary['validated']['mean_signed_residual_return'], .02)



class ApiTests(unittest.TestCase):
    def test_payload_reads_an_initialized_database(self):
        from api_support.forward_evaluation import build_forward_evaluation_payload
        self.assertTrue(build_forward_evaluation_payload(db_path=database(self))['available'])

    def test_missing_tables_are_reported_and_not_created(self):
        import tempfile
        from pathlib import Path
        from api_support.forward_evaluation import build_forward_evaluation_payload
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        empty = Path(folder.name) / 'empty.db'
        payload = build_forward_evaluation_payload(db_path=empty)
        with closing(get_connection(empty)) as conn:
            tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        self.assertEqual((payload['available'], tables), (False, 0))

    def test_demo_forward_evaluation_is_marked_fictional(self):
        from api_support.demo_mode import build_demo_mode_payload
        self.assertTrue(build_demo_mode_payload()['forward_evaluation']['demo_data'])


if __name__ == '__main__':
    unittest.main()
