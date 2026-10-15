from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.contract_research import execution_baseline, get_risk_cluster, record_observation, contract_report
from services.contract_policy import ContractPolicy
from services.liquidity_gate import evaluate_liquidity, filter_liquid_contracts
from services.scenario_selector import score_contract, rank_contracts, select_with_scenarios
from services.limit_entry import buy_limit_price, entry_invalidation, next_entry_action, LimitEntryState
from services.risk_clusters import evaluate_cluster
from services.trade_executor import TradeExecutionSession, OptionTradeExecutor
from services.position_exit_policy import evaluate_position_rungs, build_position_exit_context, shadow_exit_comparison

NOW = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)


def option(**overrides):
    result = {'symbol': 'NVDA261016C00101000', 'strike_price': 101, 'contract_type': 'call',
              'expiration_date': '2026-10-16', 'option_id': 1, 'implied_volatility': .5,
              'open_interest': 1000, 'greeks': {'delta': .5, 'gamma': .02, 'vega': .1, 'theta': -.01},
              'latest_quote': {'bid_price': 2.95, 'ask_price': 3.05, 'bid_size': 5, 'ask_size': 5,
                               'timestamp': NOW.isoformat()}}
    result.update(overrides)
    return result


def session(**overrides):
    return TradeExecutionSession(**{'available_buying_power': 20000, 'max_deployable_buying_power': 10000,
                                    'remaining_deployable_buying_power': 10000, 'base_order_qty': 1, **overrides})


class LiquidityTests(unittest.TestCase):
    def test_liquid_quote_passes_even_if_open_interest_is_unknown(self):
        gate = evaluate_liquidity(option(open_interest=None), as_of=NOW, policy=ContractPolicy(liquidity_mode='enforce'))
        self.assertTrue(gate['allowed'])

    def test_quote_age_boundary_and_future_timestamp(self):
        for seconds, expected in ((5, True), (5.001, False), (-1, False)):
            contract = option()
            contract['latest_quote']['timestamp'] = (NOW - timedelta(seconds=seconds)).isoformat()
            with self.subTest(seconds=seconds):
                self.assertEqual(evaluate_liquidity(contract, as_of=NOW)['would_allow'], expected)

    def test_depth_is_sized_to_actual_order_quantity(self):
        gate = evaluate_liquidity(option(), as_of=NOW, quantity=6)
        self.assertIn('liquidity_insufficient_depth', gate['reason_codes'])

    def test_missing_sizes_do_not_pass_using_open_interest(self):
        contract = option(open_interest=100000)
        contract['latest_quote'].pop('ask_size')
        self.assertIn('liquidity_insufficient_depth', evaluate_liquidity(contract, as_of=NOW)['reason_codes'])

    def test_wide_or_crossed_quotes_are_rejected(self):
        for bid, ask, reason in ((2.8, 3.2, 'liquidity_wide_spread'), (3.1, 3, 'liquidity_invalid_quote')):
            contract = option()
            contract['latest_quote'].update(bid_price=bid, ask_price=ask)
            with self.subTest(reason=reason):
                self.assertIn(reason, evaluate_liquidity(contract, as_of=NOW)['reason_codes'])

    def test_shadow_keeps_pool_enforcement_filters_and_records_reason(self):
        stale = option()
        stale['latest_quote']['timestamp'] = (NOW - timedelta(minutes=1)).isoformat()
        result = {'candidate_context': {'candidate_id': 'fixture'},
                  'market_context': {'option_market': {'contracts': [option(), stale]}}}
        with patch('services.contract_observations.record_contract_observation'), patch('services.candidate_lifecycle.reject') as rejection:
            shadow = filter_liquid_contracts(result, as_of=NOW)
            self.assertEqual(len(shadow['market_context']['option_market']['contracts']), 2)
            self.assertTrue(rejection.call_args.kwargs['shadow'])
            enforce = filter_liquid_contracts(result, as_of=NOW, policy=ContractPolicy(liquidity_mode='enforce'))
            self.assertEqual(len(enforce['market_context']['option_market']['contracts']), 1)
            self.assertFalse(rejection.call_args.kwargs['shadow'])


class ScenarioTests(unittest.TestCase):
    def score(self, contract=None, *, moves=(.02,), horizon_days=5):
        return score_contract(contract or option(), spot=100, moves=list(moves), horizon_days=horizon_days,
                              as_of=NOW, policy=ContractPolicy())

    def test_costs_are_separate_and_measured_at_an_unchanged_spot(self):
        score = self.score()
        self.assertEqual((round(score['spread_cost'], 9), score['theta_cost'] > 0, score['expected_iv_cost'] > 0),
                         (10.0, True, True))

    def test_expected_return_is_expected_pnl_over_premium(self):
        score = self.score()
        self.assertAlmostEqual(score['expected_return'], score['expected_pnl'] / 305)

    def test_a_crash_is_a_full_premium_loss_for_a_call(self):
        # The former Greek expansion scored this as a large gain: its gamma term grows with the square of the move.
        self.assertAlmostEqual(self.score(moves=(-.6,))['expected_pnl'], -305)

    def test_long_premium_loss_is_bounded_by_entry_cost(self):
        self.assertGreaterEqual(self.score(moves=(-.5,), horizon_days=20)['pnl_p10'], -305)

    def test_put_gains_when_the_stock_falls(self):
        put = option(symbol='NVDA261016P00099000', contract_type='put', strike_price=99)
        self.assertGreater(self.score(put, moves=(-.02,))['expected_pnl'], self.score(put, moves=(.02,))['expected_pnl'])

    def test_greeks_are_not_required(self):
        self.assertEqual(self.score(option(greeks={}))['status'], 'complete')

    def test_missing_quote_is_unknown_not_zero(self):
        self.assertEqual(self.score(option(latest_quote={}))['status'], 'missing_scenario_inputs')

    def test_quote_below_intrinsic_value_cannot_be_calibrated(self):
        deep = option(strike_price=80, latest_quote={'bid_price': 9.9, 'ask_price': 10.1})
        self.assertEqual(self.score(deep)['status'], 'iv_calibration_failed')

    def test_short_expiry_cannot_win_long_horizon(self):
        selected, scores = rank_contracts([option(expiration_date='2026-09-22')], spot=100,
                                          distribution=[.1], horizon_days=5, as_of=NOW)
        self.assertIsNone(selected)
        self.assertEqual(scores[0]['status'], 'expiry_before_horizon')

    def test_quantiles_and_tail_penalty_are_auditable(self):
        score = self.score(moves=(-.02, 0, .02))
        self.assertLess(score['pnl_p10'], score['pnl_p50'])
        self.assertLess(score['pnl_p50'], score['pnl_p90'])
        self.assertAlmostEqual(score['total_score'], score['expected_return'] - .25 * score['tail_cost'])

    def test_shadow_challenger_does_not_replace_baseline(self):
        cheaper = {**option()['latest_quote'], 'bid_price': 2.45, 'ask_price': 2.55}
        pool = [option(), option(symbol='second', latest_quote=cheaper)]
        context = {'_v2_scenario_distribution': {'returns': [.02] * 30, 'sample_size': 30,
                                                'minimum_samples': 30, 'horizon_days': 5, 'as_of': NOW.isoformat()}}
        selected, comparison = select_with_scenarios(pool, context, spot=100)
        self.assertEqual(selected['symbol'], pool[0]['symbol'])
        self.assertEqual(comparison['scenario_option_symbol'], 'second')
        with patch.dict('os.environ', {'V2_SCENARIO_MODE': 'enforce'}), patch('services.research_promotion.approved', return_value=True):
            selected, _ = select_with_scenarios(pool, context, spot=100)
        self.assertEqual(selected['symbol'], 'second')

    def test_scoring_failure_keeps_the_baseline_in_every_mode(self):
        pool = [option(), option(symbol='second')]
        context = {'_v2_scenario_distribution': {'returns': [.02] * 30, 'sample_size': 30,
                                                'minimum_samples': 30, 'horizon_days': 5, 'as_of': NOW.isoformat()}}
        failing = patch('services.scenario_selector.rank_contracts', side_effect=ZeroDivisionError('fixture'))
        with failing, self.assertLogs('services.scenario_selector', 'ERROR'):
            selected, comparison = select_with_scenarios(pool, context, spot=100)
        self.assertEqual(selected['symbol'], pool[0]['symbol'])
        self.assertEqual((comparison['status'], comparison['error']), ('scenario_error', 'ZeroDivisionError: fixture'))
        with failing, self.assertLogs('services.scenario_selector', 'ERROR'), patch.dict(
                'os.environ', {'V2_SCENARIO_MODE': 'enforce'}), patch('services.research_promotion.approved', return_value=True):
            selected, _ = select_with_scenarios(pool, context, spot=100)
        self.assertEqual(selected['symbol'], pool[0]['symbol'])

    def test_missing_distribution_preserves_baseline_without_fabrication(self):
        selected, comparison = select_with_scenarios([option()], {}, spot=100)
        self.assertEqual(selected['symbol'], option()['symbol'])
        self.assertEqual(comparison['scores'], [])


class LimitEntryTests(unittest.TestCase):
    def test_buy_tick_rounding_never_exceeds_ceiling(self):
        self.assertEqual(buy_limit_price(3.08, 3.04), 3)
        self.assertEqual(buy_limit_price(2.98, 2.97), 2.95)
        self.assertIsNone(buy_limit_price(float('nan'), 3))

    def test_ladder_and_attempt_bound(self):
        prices = [next_entry_action(LimitEntryState(quantity=2, attempt=i), bid=2, ask=3,
                                    maximum_price=2.9)['limit_price'] for i in range(4)]
        self.assertEqual(prices, [2.5, 2.6, 2.7, 2.9])
        self.assertEqual(next_entry_action(LimitEntryState(quantity=2, attempt=4), bid=2, ask=3, maximum_price=2.9)['action'], 'stop')

    def test_no_new_order_until_cancel_acknowledgement(self):
        working = LimitEntryState(quantity=3, attempt=1, phase='working', order_id='one')
        self.assertEqual(next_entry_action(working, bid=2, ask=3, maximum_price=3)['action'], 'cancel')
        pending = replace(working, phase='cancel_pending')
        self.assertEqual(next_entry_action(pending, bid=2, ask=3, maximum_price=3)['action'], 'wait_for_cancel')

    def test_partial_fill_reprices_only_remaining_quantity(self):
        state = LimitEntryState(quantity=3, attempt=1, phase='cancel_pending', filled_quantity=1)
        result = next_entry_action(state, bid=2, ask=3, maximum_price=3, cancel_confirmed=True)
        self.assertEqual(result['quantity'], 2)
        self.assertEqual(next_entry_action(replace(state, filled_quantity=3), bid=2, ask=3, maximum_price=3)['action'], 'complete')

    def test_atr_edge_and_catalyst_invalidations(self):
        reasons = entry_invalidation(quote=option()['latest_quote'], underlying_now=102,
            underlying_at_decision=100, atr_dollars=2, expected_option_value=3, limit_price=3,
            as_of=NOW, underlying_at=NOW.isoformat(), catalyst_changed=True)
        self.assertIn('underlying_move_exceeded_atr', reasons)
        self.assertIn('expected_edge_exhausted', reasons)
        self.assertIn('execution_catalyst_changed', reasons)

    def test_missing_expected_move_and_stale_underlying_cancel(self):
        reasons = entry_invalidation(quote=option()['latest_quote'], underlying_now=100,
            underlying_at_decision=100, atr_dollars=2, expected_option_value=None, limit_price=3, as_of=NOW)
        self.assertIn('execution_invalidation_inputs_missing', reasons)
        self.assertIn('execution_underlying_quote_stale', reasons)

    def test_invalidation_cancels_working_order_before_stopping(self):
        state = LimitEntryState(quantity=2, phase='working', order_id='one')
        result = next_entry_action(state, bid=2, ask=3, maximum_price=3, invalidation_reasons=['edge'])
        self.assertEqual((result['action'], result['order_id']), ('cancel', 'one'))
        self.assertEqual(next_entry_action(state, bid=2, ask=3, maximum_price=3,
                                          invalidation_reasons=['edge'], cancel_confirmed=True)['action'], 'stop')


class ClusterTests(unittest.TestCase):
    def test_executor_applies_cluster_reason_at_actual_guardrail(self):
        executor = OptionTradeExecutor(settings=SimpleNamespace(), trading_gateway=Mock(), logger=logging.getLogger('fixture'))
        book = session(cluster_option_symbols={'AI_semiconductors': {'NVDA-option'}})
        with patch('services.risk_clusters.get_risk_cluster', return_value='AI_semiconductors'), patch.dict(
                'os.environ', {'V2_RISK_CLUSTER_MODE': 'enforce'}), patch('services.research_promotion.approved', return_value=True):
            reason = executor._portfolio_guardrail_reason(option_symbol='AMD-option', underlying_symbol='AMD',
                estimated_order_cost=500, sector_key='technology', session=book)
        self.assertEqual(reason, 'portfolio_risk_cluster_limit')

    def test_executor_refreshes_liquidity_before_any_broker_submission(self):
        from tests.test_trade_executor_portfolio_guardrails import OptionTradeExecutorPortfolioGuardrailTests
        fixture = OptionTradeExecutorPortfolioGuardrailTests()
        fixture.setUp()
        stale = {**option()['latest_quote'], 'timestamp': (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
        fixture.gateway.get_execution_market_snapshot.return_value = {'option': stale}
        with patch.dict('os.environ', {'V2_LIQUIDITY_MODE': 'enforce'}), patch(
                'services.risk_clusters.get_risk_cluster', return_value='mega_cap_tech'), patch('services.research_promotion.approved', return_value=True):
            execution = fixture.executor._execute_candidate(trading_client=object(), candidate=fixture._candidate(), session=fixture._session())
        self.assertFalse(execution['submitted'])
        self.assertEqual(execution['portfolio_guardrail'], 'execution_liquidity_blocked')
        fixture.gateway.submit_option_market_order.assert_not_called()

    def test_second_semiconductor_blocks_with_room_under_sector_cap(self):
        book = session(cluster_exposure_by_key={'AI_semiconductors': 500},
                       cluster_option_symbols={'AI_semiconductors': {'NVDA-option'}})
        gate = evaluate_cluster(cluster='AI_semiconductors', option_symbol='AMD-option', estimated_cost=500,
                                 session=book, policy=ContractPolicy(cluster_mode='enforce'))
        self.assertFalse(gate['allowed'])
        self.assertEqual(gate['reason_codes'], ['portfolio_risk_cluster_limit'])
        self.assertLess(gate['metrics']['projected_exposure'], 5000)

    def test_reservation_updates_cluster_and_avoids_duplicate_position_count(self):
        book = session()
        for _ in range(2):
            OptionTradeExecutor._reserve_session_capacity(session=book, option_symbol='NVDA-option',
                sector_key='technology', estimated_order_cost=500, underlying_symbol='NVDA', risk_cluster_id='AI_semiconductors')
        self.assertEqual(book.cluster_exposure_by_key['AI_semiconductors'], 1000)
        self.assertEqual(book.cluster_option_symbols['AI_semiconductors'], {'NVDA-option'})
        self.assertEqual(book.current_option_position_count, 1)

    def test_cluster_notional_cap_applies_to_additions_to_same_position(self):
        book = session(cluster_exposure_by_key={'AI_semiconductors': 2400},
                       cluster_option_symbols={'AI_semiconductors': {'NVDA-option'}})
        gate = evaluate_cluster(cluster='AI_semiconductors', option_symbol='NVDA-option', estimated_cost=200,
                                 session=book, policy=ContractPolicy(cluster_mode='enforce'))
        self.assertFalse(gate['allowed'])

    def test_shadow_and_unknown_mapping_are_explicit(self):
        book = session(cluster_option_symbols={'AI_semiconductors': {'one'}})
        gate = evaluate_cluster(cluster='AI_semiconductors', option_symbol='two', estimated_cost=100, session=book)
        self.assertEqual((gate['would_allow'], gate['allowed']), (False, True))
        self.assertIsNone(evaluate_cluster(cluster=None, option_symbol='two', estimated_cost=100, session=book)['would_allow'])


class PositionRungTests(unittest.TestCase):
    def inputs(self, **overrides):
        return {'opened_at': (NOW - timedelta(days=6)).isoformat(), 'expected_horizon_days': 5,
                'entry_iv': .5, 'current_iv': .3, 'entry_underlying_price': 100,
                'current_underlying_price': 100.5, 'iv_at': NOW.isoformat(), 'underlying_at': NOW.isoformat(), **overrides}

    def test_calendar_fallback_is_explicit_and_shadow(self):
        result = evaluate_position_rungs(self.inputs(), as_of=NOW)
        self.assertEqual(result['active_reasons'], [])
        self.assertEqual(result['shadow_reasons'], ['CATALYST_EXPIRED', 'VOLATILITY_REGIME_CHANGE'])
        self.assertEqual(result['metrics']['expiry_clock'], 'calendar_days_fallback')

    def test_empirical_session_clock_overrides_calendar_prior(self):
        result = evaluate_position_rungs(self.inputs(sessions_held=2, p75_resolution_sessions=3), as_of=NOW)
        self.assertFalse(result['metrics']['catalyst_expired'])
        self.assertEqual(result['metrics']['expiry_clock'], 'observed_sessions')

    def test_equal_horizon_is_not_past_expiry(self):
        result = evaluate_position_rungs(self.inputs(opened_at=(NOW - timedelta(days=5)).isoformat()), as_of=NOW)
        self.assertFalse(result['metrics']['catalyst_expired'])

    def test_iv_collapse_needs_stagnant_underlying_and_fresh_quotes(self):
        for inputs in (self.inputs(current_underlying_price=110), self.inputs(iv_at=(NOW - timedelta(minutes=5)).isoformat()),
                       self.inputs(entry_iv=None)):
            with self.subTest(inputs=inputs):
                self.assertNotIn('VOLATILITY_REGIME_CHANGE', evaluate_position_rungs(inputs, as_of=NOW)['shadow_reasons'])

    def test_cached_thesis_verdict_is_never_execution_authority(self):
        result = evaluate_position_rungs(self.inputs(thesis_arbiter_action='close', thesis_verdict_at=NOW.isoformat()),
                                         as_of=NOW, policy=ContractPolicy(exit_mode='enforce'))
        self.assertIn('thesis_invalidated', result['shadow_reasons'])
        self.assertNotIn('thesis_invalidated', result['active_reasons'])

    def test_shadow_priority_and_hard_data_holds(self):
        context = evaluate_position_rungs(self.inputs(thesis_arbiter_action='close', thesis_verdict_at=NOW.isoformat()), as_of=NOW)
        action = {'reason': 'trailing_profit_stop', 'coactive_exit_reasons': ['trailing_profit_stop', 'near_expiration']}
        self.assertEqual(shadow_exit_comparison(action, context)['would_exit_reason'], 'thesis_invalidated')
        action['coactive_exit_reasons'].append('stop_loss')
        self.assertEqual(shadow_exit_comparison(action, context)['would_exit_reason'], 'stop_loss')
        self.assertEqual(shadow_exit_comparison({'reason': 'pending_exit_order'}, context)['would_exit_reason'], 'pending_exit_order')

    def test_exit_ladder_enforces_time_before_expiration_and_retains_coactive_rules(self):
        from tests.test_option_positions import _load_option_positions_module, _make_trailing_profit_config, _make_momentum_history_config
        module = _load_option_positions_module()
        strategy = sys.modules['_test_trading_support._option_positions_strategy']
        context = evaluate_position_rungs(self.inputs(), as_of=NOW, policy=ContractPolicy(exit_mode='enforce'))
        kwargs = dict(option_symbol='NVDA261016C00101000', quantity=2, unrealized_pl_pct_ratio=.1,
            broker_unrealized_pl_pct_ratio=.1, exit_rule_days_to_expiration=1, hours_to_expiration=2,
            exit_thresholds=module.ExitThresholds(dte_rule_label='fixture', take_profit_pct=60, stop_loss_pct=-35,
                force_exit_days_to_expiration=1, exit_hours_to_expiration=24, is_default_rule=False),
            position_state={}, critical_errors=[], context_notes=[], enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(module), momentum_history_config=_make_momentum_history_config(module),
            momentum_status='good', momentum_reasons=[], recently_filled_order=False, v2_exit_context=context)
        action, _ = strategy._structured_exit_action(**kwargs)
        self.assertEqual(action['reason'], 'CATALYST_EXPIRED')
        self.assertIn('near_expiration', action['coactive_exit_reasons'])
        self.assertIn('VOLATILITY_REGIME_CHANGE', action['coactive_exit_reasons'])
        kwargs['broker_unrealized_pl_pct_ratio'] = -.5
        action, _ = strategy._structured_exit_action(**kwargs)
        self.assertEqual(action['reason'], 'stop_loss')


class ContractDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO sectors(id, sector_key, name) VALUES (1, 'technology', 'Technology')")
            conn.execute("INSERT INTO industries(id, sector_id, industry_key, name) VALUES (1, 1, 'chips', 'Chips')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (1, 1, 'NVDA', 'Nvidia')")

    def test_mapping_seed_and_manual_override_survive_migration(self):
        self.assertEqual(get_risk_cluster('NVDA', db_path=self.db), 'AI_semiconductors')
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE companies SET risk_cluster_id='manual' WHERE symbol='NVDA'")
        initialize_database(db_path=self.db)
        self.assertEqual(get_risk_cluster('NVDA', db_path=self.db), 'manual')
        self.assertEqual(get_risk_cluster('AMD', db_path=self.db), 'AI_semiconductors')
        self.assertIsNone(get_risk_cluster('UNMAPPED', db_path=self.db))

    def test_empty_baseline_does_not_imply_readiness_or_zero_missed_pnl(self):
        baseline = execution_baseline(db_path=self.db)
        self.assertFalse(baseline['baseline_ready'])
        self.assertIsNone(baseline['full_fill_rate'])
        self.assertIsNone(baseline['missed_trade_opportunity_pnl'])

    def test_preview_rechecks_edge_at_each_repricing_level(self):
        from services.limit_entry import observe_entry_plan
        quote = {**option()['latest_quote'], 'ask_price': 3.0}
        candidate = {'selected_option': option(), 'candidate_context': {
            'price_absorption_gate': {'metrics': {'atr_fraction': .02}}}, 'market_context': {
            'current_stock_price': {'price': 100}, '_v2_scenario_distribution': {'returns': [.02] * 30, 'horizon_days': 5}}}
        arrival = {'option': quote, 'underlying': {'price': 100, 'timestamp': NOW.isoformat()}}
        with patch('services.scenario_selector.score_contract', return_value={'status': 'complete', 'expected_pnl': 0}):
            plan = observe_entry_plan(candidate, arrival, quantity=1, paper=True, as_of=NOW, db_path=self.db)
        self.assertEqual(plan['ladder'][0]['action'], 'submit_limit')
        self.assertEqual(plan['ladder'][3]['reason_codes'], ['expected_edge_exhausted'])

    def test_baseline_separates_paper_live_regime_and_order_type(self):
        with closing(get_connection(self.db)) as conn, conn:
            for key, paper, kind, state, filled, slippage in (
                ('one', 1, 'market', 'range', 1, .02), ('two', 0, 'market', 'range', 1, .03),
                ('three', 1, 'limit', 'range', 1, .01), ('four', 1, 'market', 'trend_up', 1, .04),
                ('five', 1, 'market', 'range', 0, None)):
                conn.execute('''INSERT INTO option_trade_executions (order_id, option_symbol, paper, order_type,
                    order_side, order_qty, filled_qty, slippage_vs_arrival, raw_json) VALUES (?, 'option', ?, ?, 'buy', 1, ?, ?, ?)''',
                    (key, paper, kind, filled, slippage, json.dumps({'market_regime_gate': {'regime_state': state}})))
        result = execution_baseline(regime_state='range', minimum_fills=1, db_path=self.db)
        self.assertEqual((result['submitted_orders'], result['measured_market_fills']), (2, 1))
        self.assertAlmostEqual(result['full_fill_rate'], .5)
        self.assertTrue(result['baseline_ready'])
        self.assertFalse(result['live_ladder_ready'])

    def test_observation_immutability_and_migration_integrity(self):
        for value in (1, 2):
            record_observation('fixture:LIQUIDITY', 'LIQUIDITY', {'value': value}, observed_at=NOW.isoformat(), db_path=self.db)
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            payload = json.loads(conn.execute('SELECT payload_json FROM contract_research_observations').fetchone()[0])
            self.assertEqual(payload, {'value': 1})
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_position_uses_filled_entry_baseline_and_dated_iv(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO position_theses (trade_execution_order_id, symbol, option_symbol, opened_at,
                expected_horizon_days) VALUES ('one', 'NVDA', 'option', ?, 5)''', ((NOW - timedelta(days=10)).isoformat(),))
            conn.execute('''INSERT INTO position_outcomes (entry_order_id, option_symbol, opened_at, iv_at_entry,
                entry_underlying_price) VALUES ('one', 'option', ?, .5, 100)''', ((NOW - timedelta(days=6)).isoformat(),))
        result = build_position_exit_context('option', {}, {'price': 100, 'timestamp': NOW.isoformat()}, as_of=NOW,
            db_path=self.db, iv_fetcher=lambda _: {'iv': .3, 'quote_at': NOW.isoformat()})
        self.assertEqual(result['metrics']['calendar_days_held'], 6)
        self.assertIn('VOLATILITY_REGIME_CHANGE', result['shadow_reasons'])

    def test_missing_thesis_never_invents_an_expiry(self):
        fetcher = Mock()
        result = build_position_exit_context('missing', {}, {}, as_of=NOW, db_path=self.db, iv_fetcher=fetcher)
        self.assertIsNone(result['metrics']['catalyst_expired'])
        fetcher.assert_not_called()

    def test_catalyst_horizon_replaces_contract_dte_and_preserves_intraday_fraction(self):
        from tests.test_thesis_validator import _sample_execution
        from services.thesis_capture import build_position_thesis_record
        from market_db_modules.thesis_validator import record_position_thesis
        execution = _sample_execution()
        execution['candidate_context'] = {'catalysts': [{'classification_status': 'classified', 'expected_horizon': 'intraday'}]}
        thesis = build_position_thesis_record(execution)
        thesis['company_id'] = 1
        thesis['manager_decision_history_id'] = None
        record_position_thesis(thesis, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            stored = conn.execute('SELECT expected_horizon_days FROM position_theses').fetchone()[0]
        self.assertAlmostEqual(stored, 1 / 24)
        self.assertEqual(thesis['raw_json']['expected_horizon_source'], 'classified_catalyst_calendar_prior')

    def test_empirical_scenarios_exclude_future_capture_and_current_catalyst(self):
        from market_db_modules.pricing_research import catalyst_return_distribution
        # Reuse the provenance fixture to create a real linked research event.
        from tests.test_v2_part4 import PricingDatabaseTests
        fixture = PricingDatabaseTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from market_db_modules.pricing_research import store_response
        store_response(fixture.event, 5, fixture.response(.02), as_of=fixture.now + timedelta(days=1), db_path=fixture.db)
        self.assertEqual(catalyst_return_distribution('guidance_raise', 5, as_of=fixture.now, db_path=fixture.db), [])
        future = fixture.now + timedelta(days=2)
        self.assertEqual(len(catalyst_return_distribution('guidance_raise', 5, as_of=future, db_path=fixture.db)), 1)
        self.assertEqual(catalyst_return_distribution('guidance_raise', 5, as_of=future,
            exclude_catalyst_id=fixture.event['catalyst_id'], db_path=fixture.db), [])


if __name__ == '__main__':
    unittest.main()
