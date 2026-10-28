"""Spec for mirroring production entries into paper accounts and closing them on production's exit."""
from __future__ import annotations

import copy
import json
import logging
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from market_db_modules import paper_traders as store
from services.paper_trader_config import PaperTraderAccount, PaperTraderSettings
from services.paper_trader_fleet import PaperTraderFleet
from tests.db_fixtures import temporary_database

CHAIN = json.loads((Path(__file__).parent / 'fixtures' / 'paper_trader_chain.json').read_text())
PRODUCTION_SYMBOL = 'XYZ261113C00105000'
PRODUCTION_CALL = next(item for item in CHAIN['buckets']['12_20'] if item['symbol'] == PRODUCTION_SYMBOL)
SPREAD_LEGS_OPEN = [{'symbol': 'XYZ261113C00105000', 'side': 'buy', 'ratio_qty': 1},
                    {'symbol': 'XYZ261113C00110000', 'side': 'sell', 'ratio_qty': 1}]
SPREAD_LEGS_CLOSE = [{'symbol': 'XYZ261113C00105000', 'side': 'sell', 'ratio_qty': 1},
                     {'symbol': 'XYZ261113C00110000', 'side': 'buy', 'ratio_qty': 1}]


def candidate():
    return {'symbol': 'XYZ', 'decision': 'call', 'selected_option_symbol': PRODUCTION_SYMBOL,
            'selected_option': copy.deepcopy(PRODUCTION_CALL),
            'market_context': {'current_stock_price': {'latest_trade_price': 100.0}}}


def execution(order_id='prod-1', submitted=True):
    return {**candidate(), 'submitted': submitted, 'order': {'id': order_id} if submitted else None,
            'estimated_order_cost': 400.0, 'order_qty': 2}


class FleetTestCase(unittest.TestCase):
    traders = ('debit_spread', 'shares')

    def setUp(self):
        temporary_database(self)
        self.now = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
        self.gateways = {}
        for name in self.traders:
            gateway = MagicMock(name=f'gateway-{name}')
            gateway.create_client.return_value = f'client-{name}'
            for method in ('submit_multileg_market_order', 'submit_stock_market_order',
                           'submit_option_market_order', 'submit_option_sell_market_order'):
                getattr(gateway, method).side_effect = self._order_ids(name, method)
            self.gateways[name] = gateway
        self.order_status = {}
        for gateway in self.gateways.values():
            gateway.get_order_status_summary.side_effect = lambda _client, *, order_id: self.order_status[order_id]
        self.chain_fetcher = MagicMock(return_value=copy.deepcopy(CHAIN))
        self.fleet = PaperTraderFleet(
            accounts=[PaperTraderAccount(name, f'key-{name}', f'secret-{name}') for name in self.traders],
            settings=PaperTraderSettings(spread_width_pct=0.05, itm_min_delta=0.70, force_exit_dte=1, max_hold_days=30),
            gateway_factory=lambda account: self.gateways[account.name],
            chain_fetcher=self.chain_fetcher,
            logger=logging.getLogger('test_paper_trader_fleet'),
            clock=lambda: self.now,
        )
        self.production_gateway = MagicMock(name='production-gateway')
        self.production_holdings = [PRODUCTION_SYMBOL]
        self.production_gateway.get_open_option_positions.side_effect = lambda _client: [
            {'option_symbol': symbol, 'underlying_symbol': 'XYZ', 'notional': 300.0, 'quantity': 2.0}
            for symbol in self.production_holdings]

    @staticmethod
    def _order_ids(name, method):
        counter = iter(range(1, 1000))
        return lambda *args, **kwargs: {'id': f'{name}-{method}-{next(counter)}', 'status': 'accepted'}

    def rows(self):
        return {row['trader']: row for row in store.active_positions()}

    def fill_all(self, price_by_trader, filled_at='2026-10-28 15:00:30+00:00'):
        for trader, row in self.rows().items():
            order_id = row['exit_order_id'] if row['status'] == 'closing' else row['entry_order_id']
            self.order_status[order_id] = {'id': order_id, 'status': 'filled',
                                           'filled_avg_price': str(price_by_trader[trader]), 'filled_at': filled_at}

    def manage(self):
        return self.fleet.manage(production_gateway=self.production_gateway, production_client='client-production')


class MirrorEntryTests(FleetTestCase):
    def test_submits_each_traders_order_and_records_it_pending(self):
        self.fleet.mirror_entry(candidate(), execution())

        self.chain_fetcher.assert_called_once_with('XYZ', 100.0)
        self.gateways['debit_spread'].submit_multileg_market_order.assert_called_once_with(
            'client-debit_spread', legs=SPREAD_LEGS_OPEN, qty=4, opening=True)
        self.gateways['shares'].submit_stock_market_order.assert_called_once_with(
            'client-shares', symbol='XYZ', qty=4, side='buy')
        rows = self.rows()
        self.assertEqual({row['status'] for row in rows.values()}, {'pending_entry'})
        self.assertEqual(rows['shares']['entry_order_id'], 'shares-submit_stock_market_order-1')
        self.assertEqual(rows['debit_spread']['production_order_id'], 'prod-1')
        self.assertEqual(rows['debit_spread']['production_option_symbol'], PRODUCTION_SYMBOL)

    def test_ignores_entries_production_did_not_submit(self):
        self.fleet.mirror_entry(candidate(), execution(submitted=False))
        self.chain_fetcher.assert_not_called()
        self.assertEqual(store.active_positions(), [])

    def test_mirrors_each_production_entry_only_once(self):
        self.fleet.mirror_entry(candidate(), execution())
        self.fleet.mirror_entry(candidate(), execution())
        self.assertEqual(self.gateways['shares'].submit_stock_market_order.call_count, 1)

    def test_one_trader_failing_does_not_stop_the_others(self):
        self.gateways['debit_spread'].submit_multileg_market_order.side_effect = RuntimeError('options level too low')
        self.fleet.mirror_entry(candidate(), execution())
        self.assertEqual(list(self.rows()), ['shares'])
        self.assertEqual(store.trader_summary()['debit_spread']['failed'], 1)

    def test_chain_failure_skips_option_traders_only(self):
        self.chain_fetcher.side_effect = RuntimeError('alpaca down')
        self.fleet.mirror_entry(candidate(), execution())
        self.assertEqual(list(self.rows()), ['shares'])
        self.assertEqual(store.trader_summary()['debit_spread']['skipped'], {'chain_unavailable': 1})


class SingleLegMirrorTests(FleetTestCase):
    traders = ('itm_single',)

    def test_single_option_leg_uses_the_single_leg_order_and_sell_to_close(self):
        self.fleet.mirror_entry(candidate(), execution())
        self.gateways['itm_single'].submit_option_market_order.assert_called_once_with(
            'client-itm_single', option_symbol='XYZ261113C00095000', qty=1)
        self.fill_all({'itm_single': 7.0})
        self.manage()
        self.production_holdings = []
        self.manage()
        self.gateways['itm_single'].submit_option_sell_market_order.assert_called_once_with(
            'client-itm_single', option_symbol='XYZ261113C00095000', qty=1)


class ManageTests(FleetTestCase):
    def setUp(self):
        super().setUp()
        self.fleet.mirror_entry(candidate(), execution())

    def test_entry_fill_is_recorded_as_a_positive_price(self):
        # Alpaca can report a multi-leg debit with either sign.
        self.fill_all({'debit_spread': -1.0, 'shares': 100.0})
        self.manage()
        rows = self.rows()
        self.assertEqual(rows['debit_spread']['status'], 'open')
        self.assertEqual(rows['debit_spread']['entry_fill_price'], 1.0)
        self.assertEqual(rows['shares']['entry_fill_price'], 100.0)

    def test_rejected_entry_is_failed(self):
        order_id = self.rows()['shares']['entry_order_id']
        self.order_status[order_id] = {'id': order_id, 'status': 'rejected', 'filled_avg_price': '', 'filled_at': ''}
        self.order_status[self.rows()['debit_spread']['entry_order_id']] = {'status': 'accepted'}
        self.manage()
        self.assertEqual(list(self.rows()), ['debit_spread'])

    def test_closes_with_explicit_reverse_legs_once_production_exits(self):
        self.fill_all({'debit_spread': 1.0, 'shares': 100.0})
        self.manage()  # fills, and sees production holding its contract
        for gateway in self.gateways.values():
            gateway.submit_multileg_market_order.reset_mock()
        self.production_holdings = []
        self.manage()

        self.gateways['debit_spread'].submit_multileg_market_order.assert_called_once_with(
            'client-debit_spread', legs=SPREAD_LEGS_CLOSE, qty=4, opening=False)
        self.gateways['shares'].submit_stock_market_order.assert_called_with(
            'client-shares', symbol='XYZ', qty=4, side='sell')
        rows = self.rows()
        self.assertEqual({row['status'] for row in rows.values()}, {'closing'})
        self.assertEqual({row['exit_reason'] for row in rows.values()}, {'production_exit'})
        for gateway in self.gateways.values():
            gateway.close_option_position.assert_not_called()

    def test_does_not_treat_a_production_position_it_never_saw_as_an_exit(self):
        self.production_holdings = []  # production's own order has not filled yet
        self.fill_all({'debit_spread': 1.0, 'shares': 100.0})
        self.manage()
        self.manage()
        self.assertEqual({row['status'] for row in self.rows().values()}, {'open'})

    def test_closes_spread_the_configured_days_before_expiry(self):
        self.fill_all({'debit_spread': 1.0, 'shares': 100.0})
        self.manage()
        self.now = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)  # 1 day before the 2026-11-13 expiry
        self.manage()
        rows = self.rows()
        self.assertEqual((rows['debit_spread']['status'], rows['debit_spread']['exit_reason']), ('closing', 'expiry_guard'))
        self.assertEqual(rows['shares']['status'], 'open')

    def test_closes_after_the_maximum_hold(self):
        self.fill_all({'debit_spread': 1.0, 'shares': 100.0})
        self.manage()
        self.now = datetime(2026, 11, 28, 15, 1, tzinfo=timezone.utc)  # over 30 days after the fill
        self.manage()
        self.assertEqual(self.rows()['shares']['exit_reason'], 'max_hold')

    def test_filled_exit_closes_and_rejected_exit_reopens(self):
        self.fill_all({'debit_spread': 1.0, 'shares': 100.0})
        self.manage()
        self.production_holdings = []
        self.manage()
        rows = self.rows()
        self.order_status[rows['debit_spread']['exit_order_id']] = {'status': 'filled', 'filled_avg_price': '1.5',
                                                                    'filled_at': '2026-11-02 15:00:00+00:00'}
        self.order_status[rows['shares']['exit_order_id']] = {'status': 'rejected', 'filled_avg_price': '',
                                                              'filled_at': ''}
        # A close that fails to submit leaves the position open for the next tick.
        self.gateways['shares'].submit_stock_market_order.side_effect = RuntimeError('still rejected')
        self.manage()
        self.assertEqual(list(self.rows()), ['shares'])
        self.assertEqual(self.rows()['shares']['status'], 'open')
        self.assertAlmostEqual(store.trader_summary()['debit_spread']['pnl'], 200.0)

    def test_unknown_production_state_closes_nothing(self):
        self.fill_all({'debit_spread': 1.0, 'shares': 100.0})
        self.manage()
        self.production_gateway.get_open_option_positions.side_effect = RuntimeError('timeout')
        self.manage()
        self.assertEqual({row['status'] for row in self.rows().values()}, {'open'})


if __name__ == '__main__':
    unittest.main()
