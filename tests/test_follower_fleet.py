"""Spec for mirroring the primary's entries into follower accounts and closing them on its exit."""
from __future__ import annotations

import copy
import json
import logging
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from market_db_modules import account_positions as store
from services.follower_fleet import FollowerFleet, build_follower_fleet
from services.trading_accounts import FollowerSettings, TradingAccount
from tests.db_fixtures import temporary_database

CHAIN = json.loads((Path(__file__).parent / 'fixtures' / 'follower_chain.json').read_text())
PRODUCTION_SYMBOL = 'XYZ261113C00105000'
PRODUCTION_CALL = next(item for item in CHAIN['buckets']['12_20'] if item['symbol'] == PRODUCTION_SYMBOL)
SPREAD_LEGS_OPEN = [{'symbol': 'XYZ261113C00105000', 'side': 'buy', 'ratio_qty': 1},
                    {'symbol': 'XYZ261113C00110000', 'side': 'sell', 'ratio_qty': 1}]
SPREAD_LEGS_CLOSE = [{'symbol': 'XYZ261113C00105000', 'side': 'sell', 'ratio_qty': 1},
                     {'symbol': 'XYZ261113C00110000', 'side': 'buy', 'ratio_qty': 1}]
PRIMARY = TradingAccount('production', 'PROD-KEY', 'PROD-SECRET', 'primary', 'single_leg', 'bucket_rules')
ORDER_METHODS = ('submit_multileg_market_order', 'submit_stock_market_order', 'submit_option_market_order',
                 'submit_option_sell_market_order', 'submit_option_sell_limit_order', 'close_option_position',
                 'cancel_order')


def follower(name, strategy=None):
    return TradingAccount(name, f'key-{name}', f'secret-{name}', 'follower', strategy or name, 'follow:production')


def candidate():
    return {'symbol': 'XYZ', 'decision': 'call', 'selected_option_symbol': PRODUCTION_SYMBOL,
            'selected_option': copy.deepcopy(PRODUCTION_CALL),
            'market_context': {'current_stock_price': {'latest_trade_price': 100.0}}}


def execution(order_id='prod-1', submitted=True):
    return {**candidate(), 'submitted': submitted, 'order': {'id': order_id} if submitted else None,
            'estimated_order_cost': 400.0, 'order_qty': 2}


class FleetTestCase(unittest.TestCase):
    followers = (follower('spreads', 'debit_spread'), follower('shares'))

    def setUp(self):
        temporary_database(self)
        self.now = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
        self.gateways = {}
        for account in (PRIMARY, *self.followers):
            gateway = MagicMock(name=f'gateway-{account.name}')
            gateway.create_client.return_value = f'client-{account.name}'
            for method in ORDER_METHODS:
                getattr(gateway, method).side_effect = self._order_ids(account.name, method)
            self.gateways[account.name] = gateway
        self.order_status = {}
        for gateway in self.gateways.values():
            gateway.get_order_status_summary.side_effect = lambda _client, *, order_id: self.order_status[order_id]
        self.production_holdings = [PRODUCTION_SYMBOL]
        self.gateways['production'].get_open_option_positions.side_effect = lambda client: [
            {'option_symbol': symbol, 'underlying_symbol': 'XYZ', 'notional': 300.0, 'quantity': 2.0}
            for symbol in self.production_holdings] if client == 'client-production' else []
        self.chain_fetcher = MagicMock(return_value=copy.deepcopy(CHAIN))
        self.fleet = FollowerFleet(
            accounts=[PRIMARY, *self.followers],
            settings=FollowerSettings(spread_width_pct=0.05, itm_min_delta=0.70, force_exit_dte=1, max_hold_days=30),
            gateway_factory=lambda account: self.gateways[account.name],
            chain_fetcher=self.chain_fetcher,
            logger=logging.getLogger('test_follower_fleet'),
            clock=lambda: self.now,
        )

    def tearDown(self):
        # Invariant: nothing ever trades on the primary account.
        for method in ORDER_METHODS:
            getattr(self.gateways['production'], method).assert_not_called()

    @staticmethod
    def _order_ids(name, method):
        counter = iter(range(1, 1000))
        return lambda *args, **kwargs: {'id': f'{name}-{method}-{next(counter)}', 'status': 'accepted'}

    def rows(self):
        return {row['account']: row for row in store.active_positions()}

    def fill_all(self, price_by_account, filled_at='2026-10-28 15:00:30+00:00'):
        for account, row in self.rows().items():
            order_id = row['exit_order_id'] if row['status'] == 'closing' else row['entry_order_id']
            self.order_status[order_id] = {'id': order_id, 'status': 'filled',
                                           'filled_avg_price': str(price_by_account[account]), 'filled_at': filled_at}


class MirrorEntryTests(FleetTestCase):
    def test_submits_each_followers_order_and_records_it_pending(self):
        self.fleet.mirror_entry(candidate(), execution())

        self.chain_fetcher.assert_called_once_with('XYZ', 100.0)
        self.gateways['spreads'].submit_multileg_market_order.assert_called_once_with(
            'client-spreads', legs=SPREAD_LEGS_OPEN, qty=4, opening=True)
        self.gateways['shares'].submit_stock_market_order.assert_called_once_with(
            'client-shares', symbol='XYZ', qty=4, side='buy')
        rows = self.rows()
        self.assertEqual({row['status'] for row in rows.values()}, {'pending_entry'})
        self.assertEqual(rows['shares']['entry_order_id'], 'shares-submit_stock_market_order-1')
        self.assertEqual((rows['spreads']['strategy'], rows['spreads']['source_order_id']), ('debit_spread', 'prod-1'))
        self.assertEqual(rows['spreads']['source_option_symbol'], PRODUCTION_SYMBOL)
        self.assertNotIn('production', rows)

    def test_ignores_entries_production_did_not_submit(self):
        self.fleet.mirror_entry(candidate(), execution(submitted=False))
        self.chain_fetcher.assert_not_called()
        self.assertEqual(store.active_positions(), [])

    def test_mirrors_each_production_entry_only_once(self):
        self.fleet.mirror_entry(candidate(), execution())
        self.fleet.mirror_entry(candidate(), execution())
        self.assertEqual(self.gateways['shares'].submit_stock_market_order.call_count, 1)

    def test_one_follower_failing_does_not_stop_the_others(self):
        self.gateways['spreads'].submit_multileg_market_order.side_effect = RuntimeError('options level too low')
        self.fleet.mirror_entry(candidate(), execution())
        self.assertEqual(list(self.rows()), ['shares'])
        self.assertEqual(store.account_summary()['spreads']['failed'], 1)

    def test_chain_failure_skips_option_strategies_only(self):
        self.chain_fetcher.side_effect = RuntimeError('alpaca down')
        self.fleet.mirror_entry(candidate(), execution())
        self.assertEqual(list(self.rows()), ['shares'])
        self.assertEqual(store.account_summary()['spreads']['skipped'], {'chain_unavailable': 1})


class SharesOnlyTests(FleetTestCase):
    followers = (follower('shares'),)

    def test_no_chain_is_fetched_without_an_option_strategy(self):
        self.fleet.mirror_entry(candidate(), execution())
        self.chain_fetcher.assert_not_called()
        self.assertEqual(list(self.rows()), ['shares'])


class SingleLegMirrorTests(FleetTestCase):
    followers = (follower('itm', 'itm_single'),)

    def test_single_option_leg_uses_the_single_leg_order_and_sell_to_close(self):
        self.fleet.mirror_entry(candidate(), execution())
        self.gateways['itm'].submit_option_market_order.assert_called_once_with(
            'client-itm', option_symbol='XYZ261113C00095000', qty=1)
        self.fill_all({'itm': 7.0})
        self.fleet.manage()
        self.production_holdings = []
        self.fleet.manage()
        self.gateways['itm'].submit_option_sell_market_order.assert_called_once_with(
            'client-itm', option_symbol='XYZ261113C00095000', qty=1)


class ManageTests(FleetTestCase):
    def setUp(self):
        super().setUp()
        self.fleet.mirror_entry(candidate(), execution())

    def test_reads_production_holdings_through_the_primary_account(self):
        self.fill_all({'spreads': 1.0, 'shares': 100.0})
        self.fleet.manage()
        self.gateways['production'].get_open_option_positions.assert_called_with('client-production')
        self.assertTrue(all(row['source_seen_at'] for row in self.rows().values()))

    def test_entry_fill_is_recorded_as_a_positive_price(self):
        # Alpaca can report a multi-leg debit with either sign.
        self.fill_all({'spreads': -1.0, 'shares': 100.0})
        self.fleet.manage()
        rows = self.rows()
        self.assertEqual(rows['spreads']['status'], 'open')
        self.assertEqual(rows['spreads']['entry_fill_price'], 1.0)
        self.assertEqual(rows['shares']['entry_fill_price'], 100.0)

    def test_rejected_entry_is_failed(self):
        rows = self.rows()
        self.order_status[rows['shares']['entry_order_id']] = {'status': 'rejected', 'filled_avg_price': '',
                                                               'filled_at': ''}
        self.order_status[rows['spreads']['entry_order_id']] = {'status': 'accepted'}
        self.fleet.manage()
        self.assertEqual(list(self.rows()), ['spreads'])

    def test_closes_with_explicit_reverse_legs_once_production_exits(self):
        self.fill_all({'spreads': 1.0, 'shares': 100.0})
        self.fleet.manage()  # fills, and sees production holding its contract
        self.production_holdings = []
        self.fleet.manage()

        self.gateways['spreads'].submit_multileg_market_order.assert_called_with(
            'client-spreads', legs=SPREAD_LEGS_CLOSE, qty=4, opening=False)
        self.gateways['shares'].submit_stock_market_order.assert_called_with(
            'client-shares', symbol='XYZ', qty=4, side='sell')
        rows = self.rows()
        self.assertEqual({row['status'] for row in rows.values()}, {'closing'})
        self.assertEqual({row['exit_reason'] for row in rows.values()}, {'production_exit'})
        for name in ('spreads', 'shares'):
            self.gateways[name].close_option_position.assert_not_called()

    def test_does_not_treat_a_production_position_it_never_saw_as_an_exit(self):
        self.production_holdings = []  # production's own order has not filled yet
        self.fill_all({'spreads': 1.0, 'shares': 100.0})
        self.fleet.manage()
        self.fleet.manage()
        self.assertEqual({row['status'] for row in self.rows().values()}, {'open'})

    def test_closes_spread_the_configured_days_before_expiry(self):
        self.fill_all({'spreads': 1.0, 'shares': 100.0})
        self.fleet.manage()
        self.now = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)  # 1 day before the 2026-11-13 expiry
        self.fleet.manage()
        rows = self.rows()
        self.assertEqual((rows['spreads']['status'], rows['spreads']['exit_reason']), ('closing', 'expiry_guard'))
        self.assertEqual(rows['shares']['status'], 'open')

    def test_closes_after_the_maximum_hold(self):
        self.fill_all({'spreads': 1.0, 'shares': 100.0})
        self.fleet.manage()
        self.now = datetime(2026, 11, 28, 15, 1, tzinfo=timezone.utc)  # over 30 days after the fill
        self.fleet.manage()
        self.assertEqual(self.rows()['shares']['exit_reason'], 'max_hold')

    def test_filled_exit_closes_and_rejected_exit_reopens(self):
        self.fill_all({'spreads': 1.0, 'shares': 100.0})
        self.fleet.manage()
        self.production_holdings = []
        self.fleet.manage()
        rows = self.rows()
        self.order_status[rows['spreads']['exit_order_id']] = {'status': 'filled', 'filled_avg_price': '1.5',
                                                               'filled_at': '2026-11-02 15:00:00+00:00'}
        self.order_status[rows['shares']['exit_order_id']] = {'status': 'rejected', 'filled_avg_price': '',
                                                              'filled_at': ''}
        # A close that fails to submit leaves the position open for the next tick.
        self.gateways['shares'].submit_stock_market_order.side_effect = RuntimeError('still rejected')
        self.fleet.manage()
        self.assertEqual(list(self.rows()), ['shares'])
        self.assertEqual(self.rows()['shares']['status'], 'open')
        self.assertAlmostEqual(store.account_summary()['spreads']['pnl'], 200.0)

    def test_unknown_production_state_closes_nothing(self):
        self.fill_all({'spreads': 1.0, 'shares': 100.0})
        self.fleet.manage()
        self.gateways['production'].get_open_option_positions.side_effect = RuntimeError('timeout')
        self.fleet.manage()
        self.assertEqual({row['status'] for row in self.rows().values()}, {'open'})


class BuildFollowerFleetTests(unittest.TestCase):
    """With only production configured, nothing new is built and no Alpaca client is created."""

    def test_production_only_builds_no_fleet_and_no_clients(self):
        with patch('services.trading_gateway.TradingClient') as client_class:
            self.assertIsNone(build_follower_fleet([PRIMARY], logger=logging.getLogger('test')))
            self.assertIsNone(build_follower_fleet([], logger=logging.getLogger('test')))
        client_class.assert_not_called()

    def test_followers_build_a_fleet_without_connecting_yet(self):
        with patch('services.trading_gateway.TradingClient') as client_class:
            fleet = build_follower_fleet([PRIMARY, follower('shares')], logger=logging.getLogger('test'),
                                         settings=FollowerSettings())
        self.assertIsInstance(fleet, FollowerFleet)
        client_class.assert_not_called()


if __name__ == '__main__':
    unittest.main()
