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
from market_db_modules.forward_comparison import (
    cluster_bootstrap,
    cluster_labels,
    comparison_report,
    promote_challenger,
)
from market_db_modules.forward_evaluation import (
    cohort_registrations,
    create_paired_candidate,
    paired_evaluation,
)
from market_db_modules.foundation_store import canonical, digest
from market_db_modules.research_observations import get_research_observation, record_research_observation
from market_db_modules.research_outcomes import collect_research_thesis_outcomes, research_route_report
from market_db_modules.research_runs import collect_run, decision_quote, start_run
from research_foundation_contracts import DECISION_OBSERVATION_PROTOCOL, ResearchObservation
from tests.research_foundation_fixtures import database
from services.challenger_evaluation import run_challenger_evaluations
from services.decision_observations import freeze_decision_observation, manager_input_payload
from services.forward_cohorts import ensure_comparison_cohort, ensure_production_cohort
from services.forward_evaluation_policy import AcceptancePolicy, ChallengerPolicy, ForwardEvaluationPolicy
from services.order_candidates import OrderCandidateBuilder
from services.paired_decisions import finalize_paired_decision
from services.research_contract import fingerprint
from services.research_marks import collect_research_marks
from services.research_promotion import champion_allows

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

    def test_paired_difference_uses_decision_quote_entry_and_closing_marks(self):
        # Challenger: ask 2.00 -> take-profit bid 3.10; incumbent: ask 2.00 -> stop at bid 1.30; $0.65 fees per side.
        self.assertAlmostEqual(self.report['paired_difference'], 108.7 - (-71.3))

    def test_paired_snapshots_never_hold_information_after_the_cutoff(self):
        with closing(get_connection(self.db)) as conn:
            cutoffs = {row[0] for row in conn.execute(
                "SELECT DISTINCT information_cutoff_time FROM candidate_snapshots WHERE candidate_id LIKE 'paired:%'")}
        self.assertEqual(cutoffs, {CUTOFF})


class ObservationTests(ForwardTestCase):
    def test_observation_holds_inputs_but_not_strategy_outputs(self):
        source = get_research_observation(self.freeze(production_decision(self.db)), db_path=self.db)['source_snapshot']
        self.assertNotIn('strategist_recommendation', manager_input_payload(source))

    def test_future_evidence_cannot_enter_an_earlier_observation(self):
        context = production_decision(self.db, article_time='2026-09-03T12:00:00+00:00')
        self.assertIsNone(self.freeze(context))

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

    def test_model_failure_retries_then_becomes_unavailable_not_cash(self):
        def broken(payload, *, model):
            raise RuntimeError('provider down')
        self.evaluate(manager=broken)
        first = paired_evaluation(self.observation_id, 'challenger', db_path=self.db)['status']
        self.evaluate(manager=broken)
        self.assertEqual((first, paired_evaluation(self.observation_id, 'challenger', db_path=self.db)['status']),
                         ('pending', 'unavailable'))

    def test_changed_implementation_is_unavailable_without_model_calls(self):
        manager = fake_manager()
        self.evaluate(manager=manager, identity=SimpleNamespace(mismatch=lambda manifest: 'implementation_changed'))
        row = paired_evaluation(self.observation_id, 'incumbent', db_path=self.db)
        self.assertEqual((row['reason'], manager.calls), ('implementation_changed', []))


class CohortTests(ForwardTestCase):
    def test_production_cohort_opens_once_per_manifest(self):
        first = ensure_production_cohort(BASE, acceptance=ACCEPTANCE, at=CUTOFF, db_path=self.db)
        again = ensure_production_cohort(BASE, acceptance=ACCEPTANCE, at=EVALUATED, db_path=self.db)
        self.assertEqual(first['cohort_id'], again['cohort_id'])

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


class RouteIsolationTests(ForwardTestCase):
    """Acceptance: a research candidate never reaches broker execution."""

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


class DecisionQuoteTests(unittest.TestCase):
    def test_quote_after_the_cutoff_is_not_an_entry(self):
        self.assertIsNone(decision_quote(FAR, '2026-09-02T14:00:00+00:00'))


class StatisticsTests(unittest.TestCase):
    def pair(self, day, symbol='NVDA', catalyst_id='catalyst:a'):
        return {'cutoff': f'2026-09-{day:02d}T15:00:00+00:00', 'symbol': symbol, 'catalyst_id': catalyst_id}

    def test_repeated_decisions_on_one_announcement_share_a_cluster(self):
        labels = cluster_labels([self.pair(2), self.pair(3), self.pair(9)])
        self.assertEqual(len(set(labels)), 1)

    def test_bootstrap_resamples_whole_clusters(self):
        result = cluster_bootstrap([5, -100, -100], [0, 1, 1], resamples=500, confidence=.9, seed=3)
        self.assertLess(result['lower_bound'], 0)


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

    def test_changed_implementation_refuses_approval(self):
        cohort = synthetic_cohort(self.db)
        report = comparison_report(cohort['cohort_id'], as_of=AFTER_WINDOW, db_path=self.db)
        with patch('market_db_modules.forward_comparison.source_hash', return_value='changed'), \
                self.assertRaises(ValueError):
            promote_challenger(report['report_id'], db_path=self.db)


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

    def test_open_contracts_are_marked_once_per_session(self):
        self.pending_position()
        first = self.collect('2026-09-03T19:52:00+00:00')
        second = self.collect('2026-09-03T19:55:00+00:00')
        self.assertEqual((first['stored'], second['status']), (1, 'already_ran'))


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

    def test_complete_outcome_is_never_rewritten(self):
        self.thesis()
        self.collect({'status': 'complete', 'residual_return': -.02, 'outcome_at': REPORTED})
        self.collect({'status': 'complete', 'residual_return': .5, 'outcome_at': REPORTED})
        summary = research_route_report(days=0, as_of=REPORTED, db_path=self.db)['outcomes_by_check_status']
        self.assertAlmostEqual(summary['validated']['mean_signed_residual_return'], .02)


class ApiTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
