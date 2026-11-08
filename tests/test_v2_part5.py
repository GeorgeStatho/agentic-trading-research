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
from market_db_modules.contract_research import (
    get_risk_cluster,
    record_observation,
)
from services.gates.contract_policy import ContractPolicy
from services.gates.liquidity_gate import evaluate_liquidity, filter_liquid_contracts
from services.options.scenario_selector import score_contract, select_with_scenarios
from services.trading.limit_entry import buy_limit_price, entry_invalidation, next_entry_action, LimitEntryState
from services.gates.risk_clusters import evaluate_cluster
from services.trading.trade_executor import TradeExecutionSession, OptionTradeExecutor
from services.options.position_exit_policy import evaluate_position_rungs

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
    def test_quote_age_boundary_and_future_timestamp(self):
        for seconds, expected in ((5, True), (5.001, False), (-1, False)):
            contract = option()
            contract['latest_quote']['timestamp'] = (NOW - timedelta(seconds=seconds)).isoformat()
            with self.subTest(seconds=seconds):
                self.assertEqual(evaluate_liquidity(contract, as_of=NOW)['would_allow'], expected)

    def test_depth_is_sized_to_actual_order_quantity(self):
        gate = evaluate_liquidity(option(), as_of=NOW, quantity=6)
        self.assertIn('liquidity_insufficient_depth', gate['reason_codes'])

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
        with patch('services.gates.contract_observations.record_contract_observation'), patch('services.runtime.candidate_lifecycle.reject') as rejection:
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

    def test_a_crash_is_a_full_premium_loss_for_a_call(self):
        # The former Greek expansion scored this as a large gain: its gamma term grows with the square of the move.
        self.assertAlmostEqual(self.score(moves=(-.6,))['expected_pnl'], -305)

    def test_shadow_challenger_does_not_replace_baseline(self):
        cheaper = {**option()['latest_quote'], 'bid_price': 2.45, 'ask_price': 2.55}
        pool = [option(), option(symbol='second', latest_quote=cheaper)]
        context = {'_v2_scenario_distribution': {'returns': [.02] * 30, 'sample_size': 30,
                                                'minimum_samples': 30, 'horizon_days': 5, 'as_of': NOW.isoformat()}}
        selected, comparison = select_with_scenarios(pool, context, spot=100)
        self.assertEqual(selected['symbol'], pool[0]['symbol'])
        self.assertEqual(comparison['scenario_option_symbol'], 'second')
        with patch.dict('os.environ', {'V2_SCENARIO_MODE': 'enforce'}), patch('services.research.research_promotion.approved', return_value=True):
            selected, _ = select_with_scenarios(pool, context, spot=100)
        self.assertEqual(selected['symbol'], 'second')

    def test_scoring_failure_keeps_the_baseline_in_every_mode(self):
        pool = [option(), option(symbol='second')]
        context = {'_v2_scenario_distribution': {'returns': [.02] * 30, 'sample_size': 30,
                                                'minimum_samples': 30, 'horizon_days': 5, 'as_of': NOW.isoformat()}}
        failing = patch('services.options.scenario_selector.rank_contracts', side_effect=ZeroDivisionError('fixture'))
        with failing, self.assertLogs('services.options.scenario_selector', 'ERROR'):
            selected, comparison = select_with_scenarios(pool, context, spot=100)
        self.assertEqual(selected['symbol'], pool[0]['symbol'])
        self.assertEqual((comparison['status'], comparison['error']), ('scenario_error', 'ZeroDivisionError: fixture'))
        with failing, self.assertLogs('services.options.scenario_selector', 'ERROR'), patch.dict(
                'os.environ', {'V2_SCENARIO_MODE': 'enforce'}), patch('services.research.research_promotion.approved', return_value=True):
            selected, _ = select_with_scenarios(pool, context, spot=100)
        self.assertEqual(selected['symbol'], pool[0]['symbol'])


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
        with patch('services.gates.risk_clusters.get_risk_cluster', return_value='AI_semiconductors'), patch.dict(
                'os.environ', {'V2_RISK_CLUSTER_MODE': 'enforce'}), patch('services.research.research_promotion.approved', return_value=True):
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
                'services.gates.risk_clusters.get_risk_cluster', return_value='mega_cap_tech'), patch('services.research.research_promotion.approved', return_value=True):
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


class PositionRungTests(unittest.TestCase):
    def inputs(self, **overrides):
        return {'opened_at': (NOW - timedelta(days=6)).isoformat(), 'expected_horizon_days': 5,
                'entry_iv': .5, 'current_iv': .3, 'entry_underlying_price': 100,
                'current_underlying_price': 100.5, 'iv_at': NOW.isoformat(), 'underlying_at': NOW.isoformat(), **overrides}

    def test_cached_thesis_verdict_is_never_execution_authority(self):
        result = evaluate_position_rungs(self.inputs(thesis_arbiter_action='close', thesis_verdict_at=NOW.isoformat()),
                                         as_of=NOW, policy=ContractPolicy(exit_mode='enforce'))
        self.assertIn('thesis_invalidated', result['shadow_reasons'])
        self.assertNotIn('thesis_invalidated', result['active_reasons'])

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
        kwargs['broker_unrealized_pl_pct_ratio'] = .1
        kwargs['v2_exit_context'] = evaluate_position_rungs(
            self.inputs(opened_at=(NOW - timedelta(days=1)).isoformat(), next_earnings_date='2026-09-21'),
            as_of=NOW, policy=ContractPolicy(exit_mode='enforce'))
        action, _ = strategy._structured_exit_action(**kwargs)
        self.assertEqual(action['reason'], 'PRE_EARNINGS')

    def test_pre_earnings_fires_from_1530_new_york_on_the_last_weekday_before_the_print(self):
        # NOW is Monday 2026-09-21 11:00 New York.
        def due(earnings, at, **inputs):
            result = evaluate_position_rungs(self.inputs(next_earnings_date=earnings, **inputs), as_of=at,
                                             policy=ContractPolicy(exit_mode='shadow'))
            self.assertEqual(result['active_reasons'], [])
            return 'PRE_EARNINGS' in result['shadow_reasons']
        self.assertFalse(due('2026-09-22', NOW))
        self.assertFalse(due('2026-09-22', NOW.replace(hour=19, minute=29)))
        self.assertTrue(due('2026-09-22', NOW.replace(hour=19, minute=30)))
        self.assertTrue(due('2026-09-21', NOW))
        self.assertTrue(due('2026-09-21', NOW, catalyst_family='earnings_positive',
                            opened_at=(NOW - timedelta(days=3)).isoformat()))
        self.assertFalse(due(None, NOW))
        self.assertTrue(due('2026-09-28', datetime(2026, 9, 25, 19, 30, tzinfo=timezone.utc)))  # Friday before a Monday print
        self.assertFalse(due('2026-09-21', NOW, catalyst_family='earnings_positive', opened_at=NOW.isoformat()))

    def test_next_earnings_date_reads_the_newest_fresh_yahoo_document(self):
        from market_db_modules.company_evidence import next_earnings_date
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'test.db'
            initialize_database(db_path=db)
            with closing(get_connection(db)) as conn, conn:
                for identity, observed, dates in (('old', '2026-09-01', ['2026-09-30']), ('new', '2026-09-10', ['2026-09-20', '2026-10-01'])):
                    record = {'metadata': {'event_key': f'yahoo_consensus:NVDA:{dates[0]}',
                                           'scheduled_events': [{'scheduled_at': date} for date in dates]}}
                    conn.execute('INSERT INTO evidence_documents VALUES (?,?,?,?,?,?,?)',
                                 (identity, 'yahoo_consensus', f'{observed}T12:00:00+00:00', None, identity, identity, json.dumps(record)))
            self.assertEqual(next_earnings_date('NVDA', as_of=NOW.isoformat(), db_path=db), '2026-10-01')
            self.assertIsNone(next_earnings_date('AMD', as_of=NOW.isoformat(), db_path=db))
            self.assertIsNone(next_earnings_date('NVDA', as_of='2026-12-01T12:00:00+00:00', db_path=db))


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

    def test_observation_immutability_and_migration_integrity(self):
        for value in (1, 2):
            record_observation('fixture:LIQUIDITY', 'LIQUIDITY', {'value': value}, observed_at=NOW.isoformat(), db_path=self.db)
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            payload = json.loads(conn.execute('SELECT payload_json FROM contract_research_observations').fetchone()[0])
            self.assertEqual(payload, {'value': 1})
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
