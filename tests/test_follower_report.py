"""Spec for the dashboard's follower report: closed trades per account, production on the same entries."""
from __future__ import annotations

import unittest
from contextlib import closing
from datetime import datetime, timezone

from db_common import get_connection
from market_db_modules import account_positions as store
from services.trading.account_strategies import OrderLeg, OrderPlan
from services.trading.trading_accounts import TradingAccount
from tests.db_fixtures import temporary_database

SPREADS = TradingAccount('spreads', 'k1', 's1', 'follower', 'debit_spread', 'follow:production')
SHARES = TradingAccount('shares', 'k2', 's2', 'follower', 'shares', 'follow:production')
SPREAD = OrderPlan('debit_spread', (OrderLeg('XYZ261113C00105000', 'buy', 'option', 1, '2026-11-13'),
                                    OrderLeg('XYZ261113C00110000', 'sell', 'option', 1, '2026-11-13')),
                   qty=4, unit_cost=100.0, multiplier=100)
SHORT_SHARES = OrderPlan('shares', (OrderLeg('XYZ', 'sell', 'stock'),), qty=4, unit_cost=100.0, multiplier=1)


def record(account, plan, source_order_id, decision='call'):
    return store.record_plan(account, source_account='production', source_order_id=source_order_id,
                             source_option_symbol='XYZ261113C00105000', underlying='XYZ', decision=decision,
                             budget=400.0, plan=plan)


def close(row_id, *, entry, exit_, opened_at, closed_at):
    store.mark_entry_fill(row_id, fill_price=entry, filled_at=opened_at)
    store.mark_closing(row_id, order_id=f'exit-{row_id}', reason='production_exit')
    store.mark_closed(row_id, fill_price=exit_, closed_at=closed_at)


def production_decision(order_id, *, pnl_pct, closed_at):
    with closing(get_connection()) as conn, conn:
        conn.execute('''INSERT INTO manager_decision_history (decision_run_at, trade_execution_order_id,
            realized_pnl_pct, closed_at) VALUES ('2026-10-15T14:00:00+00:00', ?, ?, ?)''',
                     (order_id, pnl_pct, closed_at))


class FollowerReportTests(unittest.TestCase):
    def setUp(self):
        temporary_database(self)

    def seed(self):
        close(record(SPREADS, SPREAD, 'prod-1'), entry=1.0, exit_=1.5,
              opened_at='2026-10-16T15:00:00+00:00', closed_at='2026-10-20T15:00:00+00:00')
        close(record(SHARES, SHORT_SHARES, 'prod-1'), entry=100.0, exit_=105.0,
              opened_at='2026-10-16T15:00:00+00:00', closed_at='2026-10-25T15:00:00+00:00')
        record(SHARES, SHORT_SHARES, 'prod-2')  # still pending: no closed trade
        production_decision('prod-1', pnl_pct=30.0, closed_at='2026-10-22T15:00:00+00:00')
        production_decision('prod-2', pnl_pct=None, closed_at=None)  # production still open
        production_decision('prod-unmirrored', pnl_pct=-80.0, closed_at='2026-10-21T15:00:00+00:00')

    def test_empty_database(self):
        report = store.follower_report()
        self.assertEqual((report['days'], report['accounts'], report['trades']), (0, {}, []))
        self.assertIsInstance(report['as_of'], str)

    def test_closed_trades_of_followers_and_production_sorted_by_close(self):
        self.seed()
        trades = store.follower_report()['trades']
        self.assertEqual([(trade['account'], trade['closed_at']) for trade in trades], [
            ('spreads', '2026-10-20T15:00:00+00:00'),
            ('production', '2026-10-22T15:00:00+00:00'),
            ('shares', '2026-10-25T15:00:00+00:00'),
        ])
        spreads, production, shares = trades
        self.assertEqual((spreads['strategy'], spreads['role'], spreads['underlying'], spreads['decision']),
                         ('debit_spread', 'follower', 'XYZ', 'call'))
        self.assertEqual((spreads['source_order_id'], spreads['opened_at'], spreads['exit_reason']),
                         ('prod-1', '2026-10-16T15:00:00+00:00', 'production_exit'))
        self.assertAlmostEqual(spreads['pnl'], 200.0)
        self.assertAlmostEqual(spreads['capital'], 400.0)
        self.assertAlmostEqual(spreads['pnl_pct'], 50.0)
        self.assertAlmostEqual(spreads['production_pnl_pct'], 30.0)
        # A short closed higher loses money.
        self.assertAlmostEqual(shares['pnl'], -20.0)
        self.assertAlmostEqual(shares['pnl_pct'], -5.0)

    def test_production_is_its_realized_return_on_the_entry_premium_for_mirrored_entries_only(self):
        self.seed()
        production = [trade for trade in store.follower_report()['trades'] if trade['account'] == 'production']
        self.assertEqual(len(production), 1)
        [trade] = production
        self.assertEqual((trade['role'], trade['strategy'], trade['source_order_id']),
                         ('primary', 'single_leg', 'prod-1'))
        self.assertAlmostEqual(trade['capital'], 400.0)
        self.assertAlmostEqual(trade['pnl'], 120.0)
        self.assertAlmostEqual(trade['pnl_pct'], 30.0)
        self.assertAlmostEqual(trade['production_pnl_pct'], 30.0)

    def test_accounts_are_the_account_summary(self):
        self.seed()
        report = store.follower_report()
        self.assertEqual(report['accounts'], store.account_summary())

    def test_days_window_keeps_entries_mirrored_within_it(self):
        self.seed()
        recent = store.follower_report(days=10, now=datetime.now(timezone.utc))
        self.assertEqual(len(recent['trades']), 3)
        later = store.follower_report(days=10, now=datetime(2099, 1, 1, tzinfo=timezone.utc))
        self.assertEqual((later['days'], later['accounts'], later['trades']), (10, {}, []))



class ExposureTests(unittest.TestCase):
    """P&L per dollar of underlying exposure at entry: |delta| x units x multiplier x spot.

    Return on capital mostly measures leverage on short holds; this puts every vehicle on the same footing.
    """

    def setUp(self):
        temporary_database(self)

    def seed(self):
        spread = OrderPlan(SPREAD.strategy, SPREAD.legs, qty=4, unit_cost=100.0, multiplier=100, delta=0.16)
        shares = OrderPlan(SHORT_SHARES.strategy, SHORT_SHARES.legs, qty=4, unit_cost=100.0, multiplier=1, delta=-1.0)
        for account, plan, entry, exit_ in ((SPREADS, spread, 1.0, 1.5), (SHARES, shares, 100.0, 105.0)):
            row_id = store.record_plan(account, source_account='production', source_order_id='prod-1',
                                       source_option_symbol='XYZ261113C00105000', underlying='XYZ', decision='call',
                                       budget=400.0, plan=plan, spot=100.0, source_delta=0.30)
            close(row_id, entry=entry, exit_=exit_, opened_at='2026-10-16T15:00:00+00:00',
                  closed_at='2026-10-20T15:00:00+00:00')
        production_decision('prod-1', pnl_pct=30.0, closed_at='2026-10-22T15:00:00+00:00')
        with closing(get_connection()) as conn, conn:
            conn.execute('''INSERT INTO option_trade_executions (order_id, option_symbol, paper, recorded_at, fill_price, filled_qty)
                VALUES ('prod-1', 'XYZ261113C00105000', 1, '2026-10-16T15:00:00+00:00', 2.0, 1)''')

    def test_each_trade_reports_pnl_per_dollar_of_exposure(self):
        self.seed()
        trades = {trade['account']: trade for trade in store.follower_report()['trades']}
        # Spread: $200 on 0.16 x 4 x 100 x $100 = $6,400 of exposure.
        self.assertAlmostEqual(trades['spreads']['pnl_per_exposure_pct'], 200 / 6400 * 100)
        # Short shares: -$20 on 4 x $100.
        self.assertAlmostEqual(trades['shares']['pnl_per_exposure_pct'], -5.0)
        # Production: +30% on a $2.00 premium is $0.60 a share against 0.30 x $100 = $30 of exposure.
        self.assertAlmostEqual(trades['production']['pnl_per_exposure_pct'], 2.0)

    def test_summary_averages_it_and_compares_production_on_the_same_entries(self):
        self.seed()
        summary = store.account_summary()
        self.assertAlmostEqual(summary['spreads']['avg_pnl_per_exposure_pct'], 3.125)
        self.assertAlmostEqual(summary['spreads']['production_avg_pnl_per_exposure_pct'], 2.0)
        self.assertAlmostEqual(summary['production']['avg_pnl_per_exposure_pct'], 2.0)

    def test_rows_recorded_without_delta_report_none(self):
        FollowerReportTests.seed(self)
        trades = store.follower_report()['trades']
        self.assertEqual({trade['pnl_per_exposure_pct'] for trade in trades}, {None})
        summary = store.account_summary()
        self.assertIsNone(summary['spreads']['avg_pnl_per_exposure_pct'])
        self.assertIsNone(summary['spreads']['production_avg_pnl_per_exposure_pct'])
        self.assertIsNone(summary['production']['avg_pnl_per_exposure_pct'])


if __name__ == '__main__':
    unittest.main()
