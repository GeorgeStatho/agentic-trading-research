from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from db_common import forget_setup, get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.trade_executions import record_option_trade_execution
from market_db_modules.history_records import record_manager_decision_history
from market_db_modules.candidate_lifecycle import (
    record_candidate,
    record_candidate_snapshot,
    load_candidate_lifecycle,
)
from market_db_modules.execution_telemetry import store_execution_telemetry
from market_db_modules.position_outcomes import (
    ensure_position_outcome,
    record_position_observation,
    close_position_outcome,
)
from services.candidate_lifecycle import observe
from services.execution_telemetry import fill_metrics, reconcile_execution_telemetry
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

    def test_no_fill_does_not_invent_price_or_latency(self):
        result = fill_metrics(filled_order(status='new', filled_qty='0', filled_avg_price=None), {})
        self.assertNotIn('fill_price', result)
        self.assertNotIn('seconds_to_fill', result)


class TelemetryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'telemetry.db'
        initialize_database(db_path=self.db)
        self.context = record_candidate('NVDA', VERSION, evidence=[{'id': 7, 'catalyst_id': 'event-1'}], db_path=self.db)

    def row(self, table, key='id', value=1):
        with closing(get_connection(self.db)) as conn, conn:
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
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('ALTER TABLE option_trade_executions DROP COLUMN underlying_move_while_working')
            conn.execute('ALTER TABLE manager_decision_history DROP COLUMN pipeline_version_json')
            conn.execute('DELETE FROM schema_bootstrap')  # older code set this database up
            forget_setup(self.db)
        initialize_database(db_path=self.db)
        initialize_database(db_path=self.db)
        self.assertEqual(self.row('option_trade_executions')['order_id'], 'entry')
        with closing(get_connection(self.db)) as conn, conn:
            names = {r['name'] for r in conn.execute('PRAGMA table_info(option_trade_executions)')}
        self.assertIn('underlying_move_while_working', names)

    def test_snapshot_first_write_wins_and_tampering_is_detected(self):
        record_candidate_snapshot(self.context, 'pre_gates', {'threshold': 2}, db_path=self.db)
        record_candidate_snapshot(self.context, 'pre_gates', {'threshold': 999}, db_path=self.db)
        lifecycle = load_candidate_lifecycle(self.context['candidate_id'], db_path=self.db)
        self.assertEqual(lifecycle['snapshots'][0]['snapshot']['payload']['threshold'], 2)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE candidate_snapshots SET snapshot_json='{}'")
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            load_candidate_lifecycle(self.context['candidate_id'], db_path=self.db)

    def test_fill_reconciliation_survives_restart_and_stops_at_terminal(self):
        self.entry()
        store_execution_telemetry('entry', {'order_status': 'new'}, db_path=self.db)
        gateway = Mock()
        gateway.get_order_status_summary.return_value = filled_order(status='canceled', filled_qty='1')
        report = reconcile_execution_telemetry(gateway=gateway, trading_client=object(), db_path=self.db)
        self.assertEqual(report['updated'], 1)
        self.assertEqual(self.row('option_trade_executions')['filled_qty'], 1)
        self.assertEqual(reconcile_execution_telemetry(gateway=gateway, trading_client=object(), db_path=self.db)['updated'], 0)

    def test_entry_and_reentry_do_not_share_paths(self):
        position_id, decision_id = self.position()
        with closing(get_connection(self.db)) as conn, conn:
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

    def test_failed_observation_does_not_change_trading_control_flow(self):
        with self.assertLogs('services.candidate_lifecycle', level='ERROR'):
            self.assertIsNone(observe(Mock(side_effect=RuntimeError('database unavailable'))))

    def test_coactive_exit_collection_preserves_priority_and_state(self):
        from tests.test_option_positions import _load_option_positions_module, _make_trailing_profit_config, _make_momentum_history_config
        module = _load_option_positions_module()
        strategy = sys.modules['_test_trading_support._option_positions_strategy']
        state = {'max_pnl_pct': 1.1, 'profit_protection_active': True, 'protected_profit_floor_pct': .4}
        kwargs = dict(option_symbol=SYMBOL, quantity=3, unrealized_pl_pct_ratio=.58,
                      exit_rule_days_to_expiration=1, hours_to_expiration=2,
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
