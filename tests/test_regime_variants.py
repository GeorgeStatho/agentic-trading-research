"""Shadow regime variants and the regime-sized order quantity never act; they are recorded beside production."""
from __future__ import annotations

import json
import logging
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from market_db_modules.shadow_gates import record_decision_gate_verdicts
from services.gates.entry_gates import ENTRY_GATES, entry_allowed
from services.gates.market_regime import MarketRegimeSnapshotPolicy, classify_market_regime
from services.gates.market_regime_gate import MarketRegimePolicy, evaluate_market_regime_gate
from services.gates.market_regime_variants import evaluate_regime_spy_trend_gate
from services.trading.trade_executor import OptionTradeExecutor, TradeExecutionSession

NOW = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)


def _result(decision, five, twenty, state='range'):
    return {'recommendation': {'decision': decision, 'confidence': 'high'},
            'market_context': {'market_regime': {'state': state, 'signals': {
                'spy_return_5d_pct': five, 'spy_return_20d_pct': twenty}}}}


class SpyTrendVariantTests(unittest.TestCase):
    def test_call_is_blocked_only_when_both_returns_are_down(self):
        self.assertIs(evaluate_regime_spy_trend_gate(_result('call', -1.0, -2.0))['would_allow'], False)
        self.assertIs(evaluate_regime_spy_trend_gate(_result('call', -1.0, 2.0))['would_allow'], True)
        self.assertIs(evaluate_regime_spy_trend_gate(_result('call', 1.0, -2.0))['would_allow'], True)

    def test_put_mirror_is_blocked_only_when_both_returns_are_up(self):
        self.assertIs(evaluate_regime_spy_trend_gate(_result('put', 1.0, 2.0))['would_allow'], False)
        self.assertIs(evaluate_regime_spy_trend_gate(_result('put', -1.0, 2.0))['would_allow'], True)

    def test_range_regime_no_longer_blocks_by_itself(self):
        production = evaluate_market_regime_gate(proposed_decision='call', manager_confidence='high',
                                                 regime={'state': 'range'}, policy=MarketRegimePolicy())
        self.assertIs(production['would_allow'], False)
        self.assertIs(evaluate_regime_spy_trend_gate(_result('call', 0.5, 1.0))['would_allow'], True)

    def test_missing_returns_or_direction_are_unknown(self):
        self.assertIsNone(evaluate_regime_spy_trend_gate(_result('call', None, -2.0))['would_allow'])
        self.assertIsNone(evaluate_regime_spy_trend_gate({'recommendation': {'decision': 'call'}})['would_allow'])
        self.assertIsNone(evaluate_regime_spy_trend_gate(_result('neither', -1.0, -2.0))['would_allow'])

    def test_variant_defaults_to_shadow_and_never_affects_eligibility(self):
        verdict = evaluate_regime_spy_trend_gate(_result('call', -1.0, -2.0))
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['enforcement_mode'], 'shadow')
        self.assertNotEqual(verdict['policy_version'], MarketRegimePolicy().policy_version)
        gates = {gate.result_key: {'allowed': True} for gate in ENTRY_GATES}
        self.assertTrue(entry_allowed({**gates, 'market_regime_spy_trend_gate': verdict}))

    def test_enforced_with_a_proof_it_blocks_a_call_against_the_spy_trend(self):
        from unittest.mock import patch
        from services.gates.market_regime_variants import RegimeSpyTrendPolicy
        with patch.dict('os.environ', {'REGIME_SPY_TREND_MODE': 'enforce', 'MARKET_REGIME_GATE_MODE': 'shadow'}), \
                patch('services.research.research_promotion.approved', return_value=True):
            policy = RegimeSpyTrendPolicy.from_env()
        self.assertEqual(policy.mode, 'enforce')
        blocked = evaluate_regime_spy_trend_gate(_result('call', -1.0, -2.0), policy)
        self.assertEqual((blocked['allowed'], blocked['enforcement_mode']), (False, 'enforce'))
        self.assertTrue(evaluate_regime_spy_trend_gate(_result('call', 1.0, -2.0), policy)['allowed'])
        self.assertTrue(evaluate_regime_spy_trend_gate(_result('call', None, -2.0), policy)['allowed'])

    def test_enforce_without_a_proof_stays_shadow(self):
        from unittest.mock import patch
        from services.gates.market_regime_variants import RegimeSpyTrendPolicy
        with patch.dict('os.environ', {'REGIME_SPY_TREND_MODE': 'enforce'}), \
                patch('services.research.research_promotion.approved', return_value=False):
            self.assertEqual(RegimeSpyTrendPolicy.from_env().mode, 'shadow')

    def test_it_yields_to_an_enforcing_regime_gate(self):
        from unittest.mock import patch
        from services.gates.market_regime_variants import RegimeSpyTrendPolicy
        with patch.dict('os.environ', {'REGIME_SPY_TREND_MODE': 'enforce', 'MARKET_REGIME_GATE_MODE': 'enforce'}), \
                patch('services.research.research_promotion.approved', return_value=True), \
                self.assertLogs('services.gates.market_regime_variants', level='WARNING'):
            self.assertEqual(RegimeSpyTrendPolicy.from_env().mode, 'shadow')

    def test_registered_under_its_own_stage_files_and_policy_block(self):
        from services.core.pipeline_version import gate_config
        from services.research.research_promotion import GATE_BLOCKS, GATE_FILES
        gate = next(gate for gate in ENTRY_GATES if gate.name == 'REGIME_SPY_TREND')
        self.assertEqual(gate.result_key, 'market_regime_spy_trend_gate')
        self.assertEqual(GATE_BLOCKS['REGIME_SPY_TREND'], 'regime_spy_trend')
        self.assertNotIn('Python Scripts/services/gates/market_regime_variants.py', GATE_FILES['REGIME'])
        self.assertIn('Python Scripts/services/gates/market_regime_variants.py', GATE_FILES['REGIME_SPY_TREND'])
        self.assertEqual(gate_config()['regime_spy_trend']['policy_version'], 'regime-spy-trend-v1')

    def test_api_labels_the_variant_apart_from_production(self):
        from api_support.v2_verdicts import STAGE_LABELS
        self.assertIn('variant', STAGE_LABELS['REGIME_SPY_TREND'].lower())
        self.assertNotEqual(STAGE_LABELS['REGIME_SPY_TREND'], STAGE_LABELS['REGIME'])
        self.assertIn('ENTRY_EXTENSION', STAGE_LABELS)


class SpyReturnSignalTests(unittest.TestCase):
    def classify(self, closes, at=NOW):
        return classify_market_regime(spy_closes=closes, spy_last_timestamp=at.isoformat(), vix_closes=[],
                                      macro_events=[], macro_refreshed_at=NOW.isoformat(), as_of=NOW,
                                      policy=MarketRegimeSnapshotPolicy())

    def test_snapshot_reports_spy_5_and_20_day_returns(self):
        closes = [100.0 + index for index in range(70)]
        signals = self.classify(closes)['signals']
        self.assertAlmostEqual(signals['spy_return_5d_pct'], (169 / 164 - 1) * 100, places=3)
        self.assertAlmostEqual(signals['spy_return_20d_pct'], (169 / 149 - 1) * 100, places=3)

    def test_stale_market_data_reports_no_returns(self):
        signals = self.classify([100.0 + index for index in range(70)], at=datetime(2026, 7, 1, tzinfo=timezone.utc))['signals']
        self.assertIsNone(signals['spy_return_5d_pct'])
        self.assertIsNone(signals['spy_return_20d_pct'])


class DecisionGateVerdictRowTests(unittest.TestCase):
    def test_variant_and_extension_rows_sit_beside_the_production_row(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('''CREATE TABLE decision_gate_verdicts (manager_decision_history_id INTEGER NOT NULL,
            gate_name TEXT NOT NULL, would_allow INTEGER NOT NULL, mode TEXT NOT NULL, policy_version TEXT,
            verdict_json TEXT NOT NULL, PRIMARY KEY (manager_decision_history_id, gate_name))''')
        production = evaluate_market_regime_gate(proposed_decision='call', manager_confidence='high',
                                                 regime={'state': 'range'}, policy=MarketRegimePolicy())
        variant = evaluate_regime_spy_trend_gate(_result('call', 1.0, 2.0))
        extension = {'allowed': True, 'would_allow': False, 'enforcement_mode': 'shadow',
                     'policy_version': 'entry-extension-v1'}
        record_decision_gate_verdicts(7, json.dumps({'market_regime_gate': production,
                                                     'market_regime_spy_trend_gate': variant,
                                                     'entry_extension_gate': extension}), conn=conn)
        rows = {row[0]: row[1:] for row in conn.execute(
            'SELECT gate_name, would_allow, mode, policy_version FROM decision_gate_verdicts')}
        self.assertEqual(rows['market_regime_gate'], (0, 'shadow', 'market-regime-v3-scope'))
        self.assertEqual(rows['market_regime_spy_trend_gate'], (1, 'shadow', 'regime-spy-trend-v1'))
        self.assertEqual(rows['entry_extension_gate'], (0, 'shadow', 'entry-extension-v1'))

    def test_exactly_these_registry_gates_get_a_verdict_row(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('''CREATE TABLE decision_gate_verdicts (manager_decision_history_id INTEGER NOT NULL,
            gate_name TEXT NOT NULL, would_allow INTEGER NOT NULL, mode TEXT NOT NULL, policy_version TEXT,
            verdict_json TEXT NOT NULL, PRIMARY KEY (manager_decision_history_id, gate_name))''')
        every_gate = {gate.result_key: {'allowed': True, 'would_allow': True} for gate in ENTRY_GATES}

        record_decision_gate_verdicts(7, every_gate, conn=conn)

        self.assertEqual({row[0] for row in conn.execute('SELECT gate_name FROM decision_gate_verdicts')},
                         {'entry_gate', 'market_regime_gate', 'entry_extension_gate',
                          'market_regime_spy_trend_gate', 'tradability_gate'})


class RegimeSizedQuantityTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            default_option_order_qty=1, option_contract_multiplier=100, per_order_sizing_buying_power_ratio=0.10,
            max_option_order_qty_multiplier=4, alpaca_paper=True, max_deployable_buying_power_pct=50.0,
            max_deployable_buying_power_ratio=0.50, resolve_max_premium_per_trade=lambda _buying_power: 0.0,
            max_concurrent_option_positions=5, max_option_sector_exposure_pct=100.0,
            daily_option_loss_limit_dollars=2000.0, max_symbol_entries_per_window=0, symbol_entry_window_days=7,
            symbol_stop_loss_cooldown_days=0)
        self.gateway = MagicMock()
        self.gateway.submit_option_market_order.return_value = {'id': 'order-1'}

    def execute(self, regime_gate):
        executor = OptionTradeExecutor(settings=self.settings, trading_gateway=self.gateway,
                                       logger=logging.getLogger('test_regime_variants'))
        candidate = {'company_id': 1, 'symbol': 'MSFT', 'sector_key': 'technology', 'decision': 'call',
                     'confidence': 'high', 'selected_option_symbol': 'MSFT260619C00450000',
                     'selected_option': {'latest_quote': {'ask_price': 2.0}}, 'market_regime_gate': regime_gate}
        session = TradeExecutionSession(available_buying_power=10_000.0, max_deployable_buying_power=5_000.0,
                                        remaining_deployable_buying_power=5_000.0, base_order_qty=1)
        return executor.execute_candidate(trading_client=object(), candidate=candidate, session=session)

    def test_blocking_regime_logs_zero_contracts_but_orders_the_real_size(self):
        execution = self.execute({'allowed': True, 'would_allow': False, 'sizing_multiplier': 0.0})
        self.assertTrue(execution['submitted'])
        self.assertEqual(execution['order_qty'], 2)
        self.assertEqual(self.gateway.submit_option_market_order.call_args.kwargs['qty'], 2)
        self.assertEqual(execution['regime_sized_order_qty'], 0)

    def test_allowing_regime_logs_the_same_size(self):
        execution = self.execute({'allowed': True, 'would_allow': True, 'sizing_multiplier': 1.0})
        self.assertEqual((execution['order_qty'], execution['regime_sized_order_qty']), (2, 2))

    def test_missing_multiplier_logs_no_size(self):
        self.assertIsNone(self.execute({})['regime_sized_order_qty'])


if __name__ == '__main__':
    unittest.main()
