from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from db_common import get_connection
from market_db import initialize_database, record_option_trade_execution, record_manager_decision_history
from market_db_modules.candidate_lifecycle import (record_candidate, record_candidate_snapshot,
    record_candidate_rejection, load_candidate_lifecycle, candidate_funnel)
from market_db_modules.execution_telemetry import store_execution_telemetry, execution_telemetry_report
from market_db_modules.position_outcomes import (ensure_position_outcome, record_position_observation,
    close_position_outcome, resolve_post_exit_returns, resolve_position_benchmarks, exit_attribution_report)
from services.candidate_lifecycle import GateResult, observe
from services.execution_telemetry import capture_execution_context, fill_metrics, reconcile_execution_telemetry
from services.pipeline_version import build_pipeline_version

SYMBOL = 'NVDA261016C00150000'
VERSION = {'pipeline_version': 'v2:test', 'gate_config': {'continuation': {'min_roots': 2}}}


def filled_order(**extra):
    return {'id': 'entry', 'side': 'buy', 'status': 'filled', 'filled_qty': '2', 'filled_avg_price': '2.2',
            'submitted_at': '2026-09-04T14:00:00+00:00', 'filled_at': '2026-09-04T14:00:02+00:00', **extra}


class FillMetricsTests(unittest.TestCase):
    def test_adverse_slippage_has_same_sign_on_buy_and_sell(self):
        for side, price in (('buy', '2.2'), ('sell', '1.8')):
            with self.subTest(side=side):
                result = fill_metrics(filled_order(side=side, filled_avg_price=price),
                                      {'option_mid_at_decision': 2, 'arrival_mid': 2})
                self.assertAlmostEqual(result['slippage_vs_mid'], .2)
                self.assertAlmostEqual(result['spread_paid_pct'], .1)
                self.assertEqual(result['seconds_to_fill'], 2)

    def test_cancel_with_partial_fill_is_measured(self):
        result = fill_metrics(filled_order(status='canceled', filled_qty='1'), {'arrival_mid': 2})
        self.assertEqual((result['filled_qty'], result['cancel_reason']), (1, 'canceled'))
        self.assertAlmostEqual(result['slippage_vs_arrival'], .2)

    def test_no_fill_does_not_invent_price_or_latency(self):
        result = fill_metrics(filled_order(status='new', filled_qty='0', filled_avg_price=None), {})
        self.assertNotIn('fill_price', result)
        self.assertNotIn('seconds_to_fill', result)

    def test_late_quote_cannot_stand_in_for_fill_time_quote(self):
        snapshot = {'underlying': {'price': 110, 'timestamp': '2026-09-04T14:05:00Z'},
                    'option': {'bid_price': 2, 'ask_price': 2.4, 'timestamp': '2026-09-04T14:05:00Z'}}
        result = fill_metrics(filled_order(), {'underlying_at_decision': 100, 'arrival_mid': 2}, market_snapshot=snapshot)
        self.assertNotIn('underlying_at_fill', result)
        self.assertNotIn('option_mid_move_while_working', result)
        for quote in snapshot.values():
            quote['timestamp'] = '2026-09-04T14:00:02Z'
        result = fill_metrics(filled_order(), {'underlying_at_decision': 100, 'arrival_mid': 2}, market_snapshot=snapshot)
        self.assertEqual(result['underlying_at_fill'], 110)
        self.assertAlmostEqual(result['underlying_move_while_working'], .1)

    def test_invalid_quotes_and_nonfinite_fills_are_missing(self):
        result = capture_execution_context({'selected_option': {'latest_quote': {'bid_price': 3, 'ask_price': 2}}}, {})
        self.assertIsNone(result['option_mid_at_decision'])
        self.assertNotIn('fill_price', fill_metrics(filled_order(filled_avg_price='NaN'), result))

    def test_gateway_passes_client_factories_to_quote_helpers(self):
        from services.trading_gateway import AlpacaTradingGateway
        factories = SimpleNamespace(get_option_history_client=Mock(), get_stock_history_client=Mock())
        def option(symbol, factory):
            self.assertIs(factory, factories.get_option_history_client)
            return {'symbol': symbol, 'bid_price': 2, 'ask_price': 3}
        def stock(symbol, factory):
            self.assertIs(factory, factories.get_stock_history_client)
            return {'symbol': symbol, 'price': 100}
        module = SimpleNamespace(_get_latest_option_quote=option, _get_latest_stock_price=stock)
        with patch.dict(sys.modules, {'trading_support._option_positions_market': module, 'trading_support.clients': factories}):
            result = AlpacaTradingGateway(paper=True, logger=Mock()).get_execution_market_snapshot(
                option_symbol=SYMBOL, underlying_symbol='NVDA')
        self.assertEqual(result['underlying']['price'], 100)

    def test_exit_iv_uses_a_contract_specific_chain_request(self):
        from services.trading_gateway import AlpacaTradingGateway
        from agent_helpers.market_context import option_chain
        client = Mock()
        client.get_option_chain.return_value = {SYMBOL: {'implied_volatility': .65}}
        with patch.object(option_chain, '_get_alpaca_clients', return_value={'option': client}), \
             patch.object(option_chain, 'OptionChainRequest', side_effect=lambda **kwargs: kwargs):
            result = AlpacaTradingGateway(paper=True, logger=Mock()).get_position_exit_iv({
                'symbol': SYMBOL, 'underlying_symbol': 'NVDA', 'expiration_date': '2026-10-16', 'strike': 150})
        self.assertEqual(result, .65)
        self.assertEqual(client.get_option_chain.call_args.args[0]['strike_price_gte'], 150)


class TelemetryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'telemetry.db'
        initialize_database(db_path=self.db)
        self.context = record_candidate('NVDA', VERSION, evidence=[{'id': 7, 'catalyst_id': 'event-1'}], db_path=self.db)

    def row(self, table, key='id', value=1):
        with get_connection(self.db) as conn:
            return dict(conn.execute(f'SELECT * FROM {table} WHERE {key}=?', (value,)).fetchone())

    def entry(self, order_id='entry', **extra):
        execution = {'submitted': True, 'symbol': 'NVDA', 'selected_option_symbol': SYMBOL, 'order_qty': 2,
                     'candidate_context': self.context, 'selected_option': {'implied_volatility': .5},
                     'market_context': {'sector_etf': {'ticker': 'XLK'}},
                     'order': {'id': order_id, 'side': 'buy', 'status': 'filled', 'qty': 2,
                               'submitted_at': '2026-09-04T14:00:00Z', 'type': 'market'},
                     'execution_telemetry': {'option_mid_at_decision': 2, 'arrival_mid': 2.1}, **extra}
        record_option_trade_execution(execution, paper=True, db_path=self.db)
        metrics = fill_metrics(filled_order(), execution['execution_telemetry'])
        metrics['underlying_at_fill'] = 100
        store_execution_telemetry(order_id, metrics, db_path=self.db)
        return execution

    def position(self):
        self.entry()
        decision_id = record_manager_decision_history({'symbol': 'NVDA', 'selected_option_symbol': SYMBOL,
            'trade_executed': True, 'trade_execution_order_id': 'entry', 'candidate_id': self.context['candidate_id'],
            'pipeline_version': VERSION}, db_path=self.db)
        position_id = ensure_position_outcome('entry', db_path=self.db)
        self.assertIsNotNone(position_id)
        return position_id, decision_id

    def test_migrations_are_additive_and_repeatable(self):
        self.entry()
        with get_connection(self.db) as conn:
            conn.execute('ALTER TABLE option_trade_executions DROP COLUMN underlying_move_while_working')
            conn.execute('ALTER TABLE manager_decision_history DROP COLUMN pipeline_version_json')
        initialize_database(db_path=self.db)
        initialize_database(db_path=self.db)
        self.assertEqual(self.row('option_trade_executions')['order_id'], 'entry')
        with get_connection(self.db) as conn:
            names = {r['name'] for r in conn.execute('PRAGMA table_info(option_trade_executions)')}
        self.assertIn('underlying_move_while_working', names)

    def test_snapshot_first_write_wins_and_tampering_is_detected(self):
        record_candidate_snapshot(self.context, 'pre_gates', {'threshold': 2}, db_path=self.db)
        record_candidate_snapshot(self.context, 'pre_gates', {'threshold': 999}, db_path=self.db)
        lifecycle = load_candidate_lifecycle(self.context['candidate_id'], db_path=self.db)
        self.assertEqual(lifecycle['snapshots'][0]['snapshot']['payload']['threshold'], 2)
        with get_connection(self.db) as conn:
            conn.execute("UPDATE candidate_snapshots SET snapshot_json='{}'")
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            load_candidate_lifecycle(self.context['candidate_id'], db_path=self.db)

    def test_funnel_separates_shadow_rejections_and_contains_metrics(self):
        record_candidate_snapshot(self.context, 'CONTINUATION', {'observed_roots': 1}, db_path=self.db)
        gate = GateResult(False, 'CONTINUATION', 'insufficient_roots', {'roots': 1}, {'minimum': 2})
        record_candidate_rejection(self.context, gate, shadow=True, db_path=self.db)
        result = candidate_funnel(date=self.context['created_at'][:10], db_path=self.db)
        self.assertEqual(result['stages'][0]['rejected'], 0)
        self.assertEqual(result['rejections'][0]['metrics']['roots'], 1)
        record_candidate_rejection(self.context, gate, db_path=self.db)
        result = candidate_funnel(date=self.context['created_at'][:10], db_path=self.db)
        self.assertEqual(result['stages'][0]['rejected'], 1)

    def test_fill_report_includes_partial_and_unfilled_denominators(self):
        self.entry()
        record_option_trade_execution({'submitted': True, 'symbol': 'NVDA', 'selected_option_symbol': SYMBOL, 'order_qty': 2,
            'order': {'id': 'missed', 'side': 'buy', 'qty': 2, 'status': 'canceled', 'submitted_at': '2026-09-04T15:00:00Z'}},
            paper=True, db_path=self.db)
        report = execution_telemetry_report(start='2026-09-01', end='2026-10-01', db_path=self.db)[0]
        self.assertEqual((report['submitted_orders'], report['full_fill_rate'], report['unobserved_orders']), (2, .5, 1))
        self.assertAlmostEqual(report['measured_slippage_dollars'], 40)
        self.assertEqual(report['counterfactual_samples'], 0)
        self.assertEqual(execution_telemetry_report(start='2026-10-01', db_path=self.db), [])

    def test_fill_reconciliation_survives_restart_and_stops_at_terminal(self):
        self.entry()
        store_execution_telemetry('entry', {'order_status': 'new'}, db_path=self.db)
        gateway = Mock()
        gateway.get_order_status_summary.return_value = filled_order(status='canceled', filled_qty='1')
        report = reconcile_execution_telemetry(gateway=gateway, trading_client=object(), db_path=self.db)
        self.assertEqual(report['updated'], 1)
        self.assertEqual(self.row('option_trade_executions')['filled_qty'], 1)
        self.assertEqual(reconcile_execution_telemetry(gateway=gateway, trading_client=object(), db_path=self.db)['updated'], 0)

    def test_entry_lifetime_persists_extrema_and_coactive_reasons(self):
        position_id, decision_id = self.position()
        snapshot = {'symbol': SYMBOL, 'underlying_stock_price': 110, 'broker_unrealized_pl_pct': 25,
                    'contract_type': 'call'}
        record_position_observation(snapshot, observed_at='2026-09-04T14:10:02Z', db_path=self.db)
        snapshot.update(broker_unrealized_pl_pct=-10, underlying_stock_price=95, close_submitted=True,
                        exit_reason='stop_loss', coactive_exit_reasons=['stop_loss', 'near_expiration'], implied_volatility=.6)
        record_position_observation(snapshot, observed_at='2026-09-04T14:20:02Z', version=VERSION, db_path=self.db)
        self.assertFalse(record_position_observation(snapshot, observed_at='2026-09-04T14:20:02Z', db_path=self.db))
        row = self.row('position_outcomes', value=position_id)
        self.assertEqual((row['mfe_option'], row['mae_option'], row['time_to_mfe_minutes'], row['time_to_mae_minutes']), (25, -10, 10, 20))
        self.assertEqual(row['iv_at_entry'], .5)
        self.assertEqual(row['iv_at_exit'], .6)
        with get_connection(self.db) as conn:
            close_position_outcome(decision_id, realized_pnl_pct=-12, closed_at='2026-09-04T14:21:00Z', exit_reason='stop_loss', conn=conn)
        self.assertEqual(exit_attribution_report(db_path=self.db)[0]['median_mfe_minus_realized_pct'], 37)
        lifecycle = load_candidate_lifecycle(self.context['candidate_id'], db_path=self.db)
        self.assertEqual(lifecycle['positions'][0]['entry_order_id'], 'entry')
        self.assertEqual(lifecycle['orders'][0]['candidate_id'], self.context['candidate_id'])

    def test_entry_and_reentry_do_not_share_paths(self):
        position_id, decision_id = self.position()
        with get_connection(self.db) as conn:
            close_position_outcome(decision_id, realized_pnl_pct=20, closed_at='2026-09-04T16:00:00Z', exit_reason='profit', conn=conn)
        self.entry('reentry')
        new_id = ensure_position_outcome('reentry', db_path=self.db)
        self.assertNotEqual(position_id, new_id)
        self.assertIsNone(self.row('position_outcomes', value=new_id)['mfe_option'])
        self.assertEqual(ensure_position_outcome('entry', db_path=self.db), position_id)

    def test_ambiguous_overlapping_entries_are_not_attributed(self):
        self.position()
        self.entry('overlap')
        ensure_position_outcome('overlap', db_path=self.db)
        self.assertFalse(record_position_observation({'symbol': SYMBOL, 'broker_unrealized_pl_pct': 50}, db_path=self.db))
        self.assertIsNone(self.row('position_outcomes')['mfe_option'])

    def test_post_exit_uses_sessions_across_labor_day_and_waits_for_completed_day(self):
        position_id, decision_id = self.position()
        with get_connection(self.db) as conn:
            close_position_outcome(decision_id, realized_pnl_pct=10, closed_at='2026-09-04T16:00:00Z', exit_reason='profit', conn=conn)
        bars = [{'session': day, 'close': close} for day, close in
                [('2026-09-04', 100), ('2026-09-08', 101), ('2026-09-09', 102), ('2026-09-10', 103)]]
        resolve_post_exit_returns(position_id, bars, checked_on='2026-09-10', db_path=self.db)
        row = self.row('position_outcomes')
        self.assertAlmostEqual(row['return_1d_after_exit'], 1)
        self.assertIsNone(row['return_3d_after_exit'])
        resolve_post_exit_returns(position_id, bars, checked_on='2026-09-11', db_path=self.db)
        self.assertAlmostEqual(self.row('position_outcomes')['return_3d_after_exit'], 3)

    def test_benchmark_proxy_preserves_exact_bars_and_basis(self):
        position_id, decision_id = self.position()
        with get_connection(self.db) as conn:
            close_position_outcome(decision_id, realized_pnl_pct=10, closed_at='2026-09-08T16:00:00Z', exit_reason='profit', conn=conn)
        histories = {symbol: {'provider': 'fixture', 'bars': [{'session': '2026-09-04', 'close': 100},
                     {'session': '2026-09-08', 'close': end}]} for symbol, end in [('NVDA', 110), ('SPY', 102), ('XLK', 104)]}
        resolve_position_benchmarks(position_id, histories, checked_on='2026-09-09', db_path=self.db)
        row = self.row('position_outcomes')
        self.assertAlmostEqual(row['residual_move'], 6)
        self.assertAlmostEqual(row['spy_return'], 2)
        self.assertEqual(json.loads(row['benchmark_json'])['basis'], 'entry_session_close_to_exit_session_close')

    def test_missing_underlying_session_is_not_shifted_to_the_following_day(self):
        position_id, decision_id = self.position()
        with get_connection(self.db) as conn:
            close_position_outcome(decision_id, realized_pnl_pct=0, closed_at='2026-09-04T16:00:00Z', exit_reason='profit', conn=conn)
        bars = [{'session': '2026-09-04', 'close': 100}, {'session': '2026-09-09', 'close': 102}]
        resolve_post_exit_returns(position_id, bars, checked_on='2026-09-10',
                                  sessions=['2026-09-04', '2026-09-08', '2026-09-09'], db_path=self.db)
        self.assertIsNone(self.row('position_outcomes')['return_1d_after_exit'])

    def test_daily_job_is_idempotent_and_caches_shared_benchmarks(self):
        from services.position_telemetry import refresh_post_exit_returns
        position_id, decision_id = self.position()
        with get_connection(self.db) as conn:
            close_position_outcome(decision_id, realized_pnl_pct=0, closed_at='2026-09-04T16:00:00Z', exit_reason='profit', conn=conn)
        fetcher = Mock(return_value=('fixture', [{'bar_at': day+'T04:00:00Z', 'close': price}
            for day, price in [('2026-09-04', 100), ('2026-09-08', 101), ('2026-09-09', 102), ('2026-09-10', 103)]]))
        now = datetime(2026, 9, 11, 14, tzinfo=timezone.utc)
        result = refresh_post_exit_returns(as_of=now, db_path=self.db, fetcher=fetcher)
        self.assertEqual(result, {'updated': 1, 'errors': []})
        self.assertEqual(fetcher.call_count, 3)
        self.assertEqual(refresh_post_exit_returns(as_of=now, db_path=self.db, fetcher=fetcher)['updated'], 0)
        self.assertEqual(fetcher.call_count, 3)

    def test_candidate_identity_survives_manager_builder_journal_and_thesis(self):
        from services.order_candidates import OrderCandidateBuilder
        from services.thesis_capture import build_position_thesis_record
        from market_db_modules.thesis_validator import record_position_thesis
        manager = {'candidate_context': self.context, 'candidate_id': self.context['candidate_id'],
                   'company': {'symbol': 'NVDA'}, 'recommendation': {'decision': 'call', 'confidence': 'high'},
                   'entry_gate': {'allowed': True}, 'market_regime_gate': {'allowed': True},
                   'selected_option': {'symbol': SYMBOL, 'latest_quote': {'bid_price': 1.9, 'ask_price': 2.1}}}
        candidate = OrderCandidateBuilder().build_from_manager_result(manager)
        self.assertEqual(candidate['candidate_id'], self.context['candidate_id'])
        execution = self.entry(**candidate)
        thesis = build_position_thesis_record(execution)
        thesis_id = record_position_thesis(thesis, db_path=self.db)
        row = self.row('position_theses', value=thesis_id)
        self.assertEqual(row['candidate_id'], self.context['candidate_id'])
        self.assertEqual(json.loads(row['pipeline_version_json']), VERSION)

    def test_put_underlying_extrema_follow_position_direction(self):
        self.position()
        record_position_observation({'symbol': SYMBOL, 'contract_type': 'put', 'underlying_stock_price': 90,
                                     'broker_unrealized_pl_pct': 20}, observed_at='2026-09-04T14:10:02Z', db_path=self.db)
        row = self.row('position_outcomes')
        self.assertAlmostEqual(row['mfe_underlying'], 10)
        self.assertEqual(row['mae_underlying'], 0)


class VersionAndGateTests(unittest.TestCase):
    def test_manifest_is_stable_but_threshold_changes_are_visible(self):
        from services.config import FrontMainSettings
        settings = FrontMainSettings.from_env()
        first = build_pipeline_version(settings=settings)
        self.assertEqual(first, build_pipeline_version(settings=settings))
        changed = build_pipeline_version(settings=replace(settings, execute_medium_confidence_trades=not settings.execute_medium_confidence_trades))
        self.assertNotEqual(first['pipeline_version'], changed['pipeline_version'])
        with patch.dict('os.environ', {'ALPACA_API_KEY': 'never-store-this-secret'}):
            self.assertNotIn('never-store-this-secret', json.dumps(build_pipeline_version(settings=settings)))

    def test_model_provider_change_is_part_of_version(self):
        with patch.dict('os.environ', {'LLM_PROVIDER': 'ollama'}):
            first = build_pipeline_version()
        with patch.dict('os.environ', {'LLM_PROVIDER': 'vertex'}):
            second = build_pipeline_version()
        self.assertNotEqual(first['pipeline_version'], second['pipeline_version'])
        self.assertEqual(second['model_provider'], 'vertex')

    def test_failed_observation_does_not_change_trading_control_flow(self):
        with self.assertLogs('services.candidate_lifecycle', level='ERROR'):
            self.assertIsNone(observe(Mock(side_effect=RuntimeError('database unavailable'))))

    def test_canonical_gate_adapter_records_enforced_and_shadow_results(self):
        from services.candidate_lifecycle import record_gate
        context = {'candidate_id': 'candidate', 'pipeline_version': VERSION}
        with patch('services.candidate_lifecycle.checkpoint'), patch('services.candidate_lifecycle.record_candidate_rejection') as writer:
            record_gate(context, 'CONTINUATION', {'allowed': True, 'would_allow': False, 'reason_codes': ['roots']})
            self.assertTrue(writer.call_args.kwargs['shadow'])
            record_gate(context, 'CONTINUATION', {'allowed': False, 'reason_codes': ['roots']})
            self.assertFalse(writer.call_args.kwargs['shadow'])

    def test_coactive_exit_collection_preserves_priority_and_state(self):
        from tests.test_option_positions import _load_option_positions_module, _make_trailing_profit_config, _make_momentum_history_config
        module = _load_option_positions_module()
        strategy = sys.modules['_test_trading_support._option_positions_strategy']
        state = {'max_pnl_pct': 1.1, 'profit_protection_active': True, 'protected_profit_floor_pct': .4}
        kwargs = dict(option_symbol=SYMBOL, quantity=3, unrealized_pl_pct_ratio=.58,
                      days_to_expiration=1, hours_to_expiration=2,
                      exit_thresholds=module.ExitThresholds(dte_rule_label='test', take_profit_pct=60,
                          stop_loss_pct=-35, force_exit_days_to_expiration=7, exit_hours_to_expiration=168, is_default_rule=False),
                      position_state=state, critical_errors=[], context_notes=[], enable_trailing_profit=True,
                      trailing_profit_config=_make_trailing_profit_config(module, floor_100_pct=.6),
                      momentum_history_config=_make_momentum_history_config(module), momentum_status='good',
                      momentum_reasons=[], recently_filled_order=False)
        original = deepcopy(state)
        with patch.object(sys.modules['_test_trading_support._option_positions_state'], 'datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)
            expected, expected_state = strategy._structured_exit_action_impl(**kwargs)
            actual, actual_state = strategy._structured_exit_action(**kwargs)
        reasons = actual.pop('coactive_exit_reasons')
        self.assertEqual(actual, expected)
        self.assertEqual(actual_state, expected_state)
        self.assertEqual(state, original)
        self.assertEqual(reasons[0], 'near_expiration')
        self.assertIn('trailing_profit_stop', reasons)


if __name__ == '__main__':
    unittest.main()
