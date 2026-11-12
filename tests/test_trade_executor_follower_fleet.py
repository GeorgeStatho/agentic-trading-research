"""Production entries hand off to the follower fleet without the followers ever affecting production."""
from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.trading.trade_executor import OptionTradeExecutor, TradeExecutionSession


class TradeExecutorFollowerFleetTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            default_option_order_qty=1, option_contract_multiplier=100, per_order_sizing_buying_power_ratio=0.10,
            max_option_order_qty_multiplier=2, alpaca_paper=True, max_deployable_buying_power_pct=50.0,
            max_deployable_buying_power_ratio=0.50, resolve_max_premium_per_trade=lambda _buying_power: 0.0,
            max_concurrent_option_positions=2, max_option_sector_exposure_pct=50.0,
            daily_option_loss_limit_dollars=2000.0, max_symbol_entries_per_window=0, symbol_entry_window_days=7,
            symbol_stop_loss_cooldown_days=0)
        self.gateway = MagicMock()
        self.gateway.submit_option_market_order.return_value = {'id': 'order-1'}
        self.fleet = MagicMock()

    def executor(self, follower_fleet):
        return OptionTradeExecutor(settings=self.settings, trading_gateway=self.gateway, follower_fleet=follower_fleet,
                                   logger=logging.getLogger('test_trade_executor_follower_fleet'))

    @staticmethod
    def candidate():
        return {'company_id': 1, 'symbol': 'MSFT', 'sector_key': 'technology', 'decision': 'call',
                'confidence': 'high', 'selected_option_symbol': 'MSFT260619C00450000',
                'selected_option': {'latest_quote': {'ask_price': 10.0}}}

    @staticmethod
    def session(**overrides):
        values = {'available_buying_power': 10_000.0, 'max_deployable_buying_power': 5_000.0,
                  'remaining_deployable_buying_power': 5_000.0, 'base_order_qty': 1, **overrides}
        return TradeExecutionSession(**values)

    def execute(self, executor, **session_overrides):
        return executor.execute_candidate(trading_client=object(), candidate=self.candidate(),
                                          session=self.session(**session_overrides))

    def test_submitted_entry_is_mirrored_with_its_execution(self):
        execution = self.execute(self.executor(self.fleet))
        self.assertTrue(execution['submitted'])
        self.fleet.mirror_entry.assert_called_once()
        mirrored_candidate, mirrored_execution = self.fleet.mirror_entry.call_args.args
        self.assertEqual(mirrored_candidate['selected_option_symbol'], 'MSFT260619C00450000')
        self.assertEqual(mirrored_execution['order'], {'id': 'order-1'})
        self.assertEqual(mirrored_execution['estimated_order_cost'], execution['estimated_order_cost'])
        self.assertTrue(mirrored_execution['submitted'])

    def test_refused_entry_is_not_mirrored(self):
        self.settings.max_concurrent_option_positions = 1
        execution = self.execute(self.executor(self.fleet), current_option_position_count=1)
        self.assertFalse(execution['submitted'])
        self.fleet.mirror_entry.assert_not_called()

    def test_premium_cap_refusal_is_offered_to_followers_with_the_cap(self):
        execution = self.execute(self.executor(self.fleet), max_premium_per_trade=300.0)
        self.assertFalse(execution['submitted'])
        self.fleet.mirror_entry.assert_not_called()
        self.fleet.mirror_blocked.assert_called_once()
        offered, offered_execution, cap = self.fleet.mirror_blocked.call_args.args
        self.assertEqual(offered['selected_option_symbol'], 'MSFT260619C00450000')
        self.assertEqual((offered_execution['portfolio_guardrail'], cap), ('premium_per_trade_cap', 300.0))

    def test_other_refusals_are_never_offered_to_followers(self):
        self.settings.max_concurrent_option_positions = 1
        self.execute(self.executor(self.fleet), current_option_position_count=1)
        self.fleet.mirror_blocked.assert_not_called()

    def test_followers_failing_on_a_blocked_pick_leave_production_unchanged(self):
        baseline = self.execute(self.executor(None), max_premium_per_trade=300.0)
        self.fleet.mirror_blocked.side_effect = RuntimeError('follower account down')
        execution = self.execute(self.executor(self.fleet), max_premium_per_trade=300.0)
        for key in ('submitted', 'order', 'order_qty', 'estimated_order_cost', 'error', 'portfolio_guardrail'):
            self.assertEqual(execution[key], baseline[key], key)

    def test_fleet_failure_leaves_production_execution_unchanged(self):
        baseline = self.execute(self.executor(None))
        self.fleet.mirror_entry.side_effect = RuntimeError('follower account down')
        execution = self.execute(self.executor(self.fleet))
        self.assertTrue(execution['submitted'])
        self.fleet.mirror_entry.assert_called_once()
        for key in ('submitted', 'order', 'order_qty', 'estimated_order_cost', 'error', 'portfolio_guardrail'):
            self.assertEqual(execution[key], baseline[key], key)


if __name__ == '__main__':
    unittest.main()
