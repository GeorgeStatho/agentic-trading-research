"""Spec for follower accounts that also take premium-cap-blocked picks and exit by their own policies."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from market_db_modules import account_positions as store
from services.core.settings import FollowerSettings
from services.trading.trading_accounts import TradingAccount
from tests import test_follower_fleet as base

BLOCKED = 'premium_cap_blocked'


def account(name, strategy=None, *, blocked=False, policy='follow:production'):
    return TradingAccount(name, f'key-{name}', f'secret-{name}', 'follower', strategy or name, policy, blocked)


def blocked_execution(candidate_id='cand-1'):
    candidate = {**base.candidate(), 'candidate_context': {'candidate_id': candidate_id}}
    return {**candidate, 'submitted': False, 'order': None, 'estimated_order_cost': 1000.0, 'order_qty': 0,
            'portfolio_guardrail': 'premium_per_trade_cap'}


class BlockedPickTests(base.FleetTestCase):
    followers = (account('spreads', 'debit_spread', blocked=True), account('shares', blocked=True),
                 account('plain_shares', 'shares'))

    def blocked(self, cap=300.0, candidate_id='cand-1'):
        item = blocked_execution(candidate_id)
        self.fleet.mirror_blocked(item, item, cap)

    def test_opted_in_followers_trade_the_pick_sized_by_the_cap_and_tagged(self):
        self.blocked(300.0)
        rows = self.rows()
        self.assertEqual(sorted(rows), ['shares', 'spreads'])  # plain_shares did not opt in
        self.assertEqual({row['entry_kind'] for row in rows.values()}, {BLOCKED})
        self.assertEqual({row['budget'] for row in rows.values()}, {300.0})
        self.assertTrue(all(row['source_order_id'] == 'blocked:cand-1' for row in rows.values()))
        self.gateways['shares'].submit_stock_market_order.assert_called_once_with(
            'client-shares', symbol='XYZ', qty=3, side='buy')
        self.gateways['spreads'].submit_multileg_market_order.assert_called_once_with(
            'client-spreads', legs=base.SPREAD_LEGS_OPEN, qty=3, opening=True)
        self.gateways['plain_shares'].submit_stock_market_order.assert_not_called()

    def test_copied_entries_stay_tagged_copy(self):
        self.fleet.mirror_entry(base.candidate(), base.execution())
        self.assertEqual({row['entry_kind'] for row in self.rows().values()}, {'copy'})

    def test_a_structure_costing_more_than_the_cap_is_skipped_not_forced_to_one_unit(self):
        self.blocked(50.0)  # one spread costs about $100 and one share $100
        self.assertEqual(self.rows(), {})
        summary = store.account_summary(entry_kind=BLOCKED)
        self.assertEqual(summary['spreads']['skipped'], {'over_premium_cap': 1})
        self.assertEqual(summary['shares']['skipped'], {'over_premium_cap': 1})
        self.gateways['shares'].submit_stock_market_order.assert_not_called()
        self.gateways['spreads'].submit_multileg_market_order.assert_not_called()

    def test_each_blocked_pick_is_taken_once(self):
        self.blocked()
        self.blocked()
        self.assertEqual(self.gateways['shares'].submit_stock_market_order.call_count, 1)
        self.blocked(candidate_id='cand-2')
        self.assertEqual(self.gateways['shares'].submit_stock_market_order.call_count, 2)

    def test_a_pick_without_a_candidate_id_is_still_keyed_per_option_and_time(self):
        item = blocked_execution()
        item['candidate_context'] = None
        self.fleet.mirror_blocked(item, item, 300.0)
        self.assertTrue(self.rows()['shares']['source_order_id'].startswith(f'blocked:{base.PRODUCTION_SYMBOL}:'))

    def test_blocked_positions_ignore_production_and_use_the_followers_own_exits(self):
        self.blocked()
        self.fill_all({'spreads': 1.0, 'shares': 100.0})
        self.fleet.manage()
        self.production_holdings = []
        self.fleet.manage()
        self.now = datetime(2026, 10, 30, 15, 1, tzinfo=timezone.utc)  # two days on: no special clock
        self.fleet.manage()
        self.assertEqual({row['status'] for row in self.rows().values()}, {'open'})
        self.now = datetime(2026, 11, 28, 15, 1, tzinfo=timezone.utc)  # past the spread's expiry and max hold
        self.fleet.manage()
        self.assertEqual({name: row['exit_reason'] for name, row in self.rows().items()},
                         {'spreads': 'expiry_guard', 'shares': 'max_hold'})

    def test_blocked_evidence_is_kept_out_of_the_production_comparison(self):
        self.fleet.mirror_entry(base.candidate(), base.execution())
        self.blocked()
        self.assertEqual(store.account_summary()['shares']['active'], 1)
        self.assertEqual(store.account_summary(entry_kind=BLOCKED)['shares']['active'], 1)
        self.assertEqual(store.follower_report()['trades'], [])


class ExitPolicyTests(base.FleetTestCase):
    followers = (account('stop', 'shares', policy='stock_stop'),
                 account('partial', 'shares', policy='partial_profit'),
                 account('plain', 'shares'))

    def setUp(self):
        super().setUp()
        self.snapshot = {'option': {'mid_price': None}, 'underlying': {'price': 100.0, 'mid_price': 100.0}}
        for gateway in self.gateways.values():
            gateway.get_execution_market_snapshot.side_effect = lambda **_: self.snapshot
        self.fleet.mirror_entry(base.candidate(), base.execution())
        self.fill_all({'stop': 100.0, 'partial': 100.0, 'plain': 100.0})
        self.fleet.manage()

    def statuses(self):
        return {name: (row['status'], row['exit_reason']) for name, row in self.rows().items()}

    def test_stock_stop_closes_when_the_stock_moves_against_the_call(self):
        self.snapshot['underlying'] = {'price': 97.5, 'mid_price': 97.5}  # beyond the default 2% stop
        self.fleet.manage()
        self.assertEqual(self.statuses()['stop'], ('closing', 'stock_stop'))
        self.assertEqual(self.statuses()['plain'], ('open', None))
        self.gateways['stop'].submit_stock_market_order.assert_called_with(
            'client-stop', symbol='XYZ', qty=4, side='sell')

    def test_stock_stop_holds_inside_the_stop_and_for_a_move_in_favour(self):
        for price in (98.5, 108.0):
            self.snapshot['underlying'] = {'price': price, 'mid_price': price}
            self.fleet.manage()
        self.assertEqual(self.statuses()['stop'], ('open', None))

    def test_stock_stop_for_a_put_triggers_on_a_rise(self):
        row = self.rows()['stop']
        self.assertTrue(self.fleet._stock_stop_hit({**row, 'decision': 'put'}, 102.5))
        self.assertFalse(self.fleet._stock_stop_hit({**row, 'decision': 'put'}, 97.0))

    def test_unreadable_price_never_closes_or_trims(self):
        self.snapshot['underlying'] = {'price': None, 'mid_price': None}
        self.fleet.manage()
        self.assertEqual({row['status'] for row in self.rows().values()}, {'open'})

    def test_follow_production_never_reads_market_data(self):
        self.gateways['plain'].get_execution_market_snapshot.reset_mock()
        self.fleet.manage()
        self.gateways['plain'].get_execution_market_snapshot.assert_not_called()

    def test_partial_profit_sells_a_fraction_once_and_the_rest_follows_production(self):
        self.snapshot['underlying'] = {'price': 160.0, 'mid_price': 160.0}  # +60% on the shares
        self.fleet.manage()
        self.gateways['partial'].submit_stock_market_order.assert_called_with(
            'client-partial', symbol='XYZ', qty=2, side='sell')
        row = self.rows()['partial']
        self.assertEqual((row['status'], row['qty'], row['partial_qty']), ('open', 4, None))
        self.order_status[row['partial_order_id']] = {'status': 'filled', 'filled_qty': '2',
                                                      'filled_avg_price': '160', 'filled_at': '2026-10-28 16:00:00+00:00'}
        self.fleet.manage()
        row = self.rows()['partial']
        self.assertEqual((row['qty'], row['partial_qty'], row['partial_fill_price']), (2, 2, 160.0))
        self.fleet.manage()  # does not trim again
        self.assertEqual(self.gateways['partial'].submit_stock_market_order.call_count, 2)  # entry + one trim

        self.production_holdings = []
        self.fleet.manage()
        self.gateways['partial'].submit_stock_market_order.assert_called_with(
            'client-partial', symbol='XYZ', qty=2, side='sell')
        row = self.rows()['partial']
        self.order_status[row['exit_order_id']] = {'status': 'filled', 'filled_avg_price': '170',
                                                   'filled_at': '2026-10-29 15:00:00+00:00'}
        self.fleet.manage()
        # (160-100) x 2 on the trimmed slice plus (170-100) x 2 on the rest, over 4 x 100.
        self.assertAlmostEqual(store.account_summary()['partial']['pnl'], 260.0)
        self.assertAlmostEqual(store.account_summary()['partial']['capital'], 400.0)

    def test_nothing_exits_while_a_trim_is_still_working(self):
        self.snapshot['underlying'] = {'price': 160.0, 'mid_price': 160.0}
        self.fleet.manage()
        self.order_status[self.rows()['partial']['partial_order_id']] = {'status': 'accepted'}
        self.production_holdings = []
        self.fleet.manage()
        self.assertEqual(self.rows()['partial']['status'], 'open')

    def test_a_trim_that_never_fills_is_not_retried(self):
        self.snapshot['underlying'] = {'price': 160.0, 'mid_price': 160.0}
        self.fleet.manage()
        self.order_status[self.rows()['partial']['partial_order_id']] = {'status': 'rejected', 'filled_qty': '0'}
        self.fleet.manage()
        self.fleet.manage()
        self.assertEqual(self.gateways['partial'].submit_stock_market_order.call_count, 2)
        self.assertEqual(self.rows()['partial']['qty'], 4)

    def test_below_the_profit_trigger_nothing_is_sold(self):
        self.snapshot['underlying'] = {'price': 120.0, 'mid_price': 120.0}
        self.fleet.manage()
        self.assertEqual(self.gateways['partial'].submit_stock_market_order.call_count, 1)  # the entry only


class SpreadPartialProfitTests(base.FleetTestCase):
    followers = (account('spreads', 'debit_spread', policy='partial_profit'),)

    def test_spread_value_is_the_long_mid_less_the_short_mid(self):
        quotes = {'XYZ261113C00105000': 3.0, 'XYZ261113C00110000': 1.0}
        for gateway in self.gateways.values():
            gateway.get_execution_market_snapshot.side_effect = lambda *, option_symbol, underlying_symbol: {
                'option': {'mid_price': quotes.get(option_symbol)}, 'underlying': {'price': 100.0}}
        self.fleet.mirror_entry(base.candidate(), base.execution())
        self.fill_all({'spreads': 1.0})
        self.fleet.manage()  # a $2.00 spread against a $1.00 entry is +100%
        self.gateways['spreads'].submit_multileg_market_order.assert_called_with(
            'client-spreads', legs=base.SPREAD_LEGS_CLOSE, qty=2, opening=False)

    def test_a_single_unit_cannot_be_trimmed(self):
        self.fleet.mirror_entry(base.candidate(), {**base.execution(), 'estimated_order_cost': 100.0})
        self.fill_all({'spreads': 1.0})
        self.assertEqual(self.rows()['spreads']['qty'], 1)
        for gateway in self.gateways.values():
            gateway.get_execution_market_snapshot.side_effect = lambda **_: {
                'option': {'mid_price': 9.0}, 'underlying': {'price': 100.0}}
        self.fleet.manage()
        self.assertEqual(self.gateways['spreads'].submit_multileg_market_order.call_count, 1)


class SettingsTests(unittest.TestCase):
    def test_defaults_and_env_overrides(self):
        defaults = FollowerSettings()
        self.assertEqual(defaults.stock_stop_pct, 0.02)
        self.assertFalse(hasattr(defaults, 'blocked_max_hold_days'))
        self.assertEqual((defaults.partial_profit_pct, defaults.partial_profit_fraction), (0.5, 0.5))
        env = {'FOLLOWER_STOCK_STOP_PCT': '0.05',
               'FOLLOWER_PARTIAL_PROFIT_PCT': '0.4', 'FOLLOWER_PARTIAL_PROFIT_FRACTION': '0.25'}
        loaded = FollowerSettings.from_environ(env)
        self.assertEqual((loaded.stock_stop_pct, loaded.partial_profit_pct, loaded.partial_profit_fraction),
                         (0.05, 0.4, 0.25))


if __name__ == '__main__':
    unittest.main()
