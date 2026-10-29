from __future__ import annotations

import unittest
from contextlib import closing

from db_common import get_connection
from market_db_modules import account_positions as store
from services.account_strategies import OrderLeg, OrderPlan, Skip
from services.trading_accounts import TradingAccount
from tests.db_fixtures import temporary_database

SPREADS = TradingAccount('spreads', 'k1', 's1', 'follower', 'debit_spread', 'follow:production')
SHARES = TradingAccount('shares', 'k2', 's2', 'follower', 'shares', 'follow:production')
LONGER = TradingAccount('longer_dte', 'k3', 's3', 'follower', 'longer_dte', 'follow:production')

SPREAD = OrderPlan('debit_spread', (OrderLeg('XYZ261113C00105000', 'buy', 'option', 1, '2026-11-13'),
                                    OrderLeg('XYZ261113C00110000', 'sell', 'option', 1, '2026-11-13')),
                   qty=4, unit_cost=100.0, multiplier=100)
SHORT_SHARES = OrderPlan('shares', (OrderLeg('XYZ', 'sell', 'stock'),), qty=4, unit_cost=100.0, multiplier=1)


def record(account, plan, *, source_order_id='prod-1'):
    return store.record_plan(account, source_account='production', source_order_id=source_order_id,
                             source_option_symbol='XYZ261113C00105000', underlying='XYZ', decision='call',
                             budget=400.0, plan=plan)


def close(row_id, *, entry, exit_):
    store.mark_entry_fill(row_id, fill_price=entry, filled_at='2026-10-28T15:00:00+00:00')
    store.mark_closing(row_id, order_id=f'exit-{row_id}', reason='production_exit')
    store.mark_closed(row_id, fill_price=exit_, closed_at='2026-11-02T15:00:00+00:00')


class AccountPositionsStoreTests(unittest.TestCase):
    def setUp(self):
        temporary_database(self)

    def test_plan_is_recorded_once_per_account_and_source_entry(self):
        row_id = record(SPREADS, SPREAD)
        self.assertIsInstance(row_id, int)
        self.assertIsNone(record(SPREADS, SPREAD))
        self.assertIsInstance(record(SPREADS, SPREAD, source_order_id='prod-2'), int)

    def test_rows_carry_the_account_identity_and_decoded_legs(self):
        record(SPREADS, SPREAD)
        [row] = store.active_positions()
        self.assertEqual((row['account'], row['role'], row['strategy'], row['exit_policy']),
                         ('spreads', 'follower', 'debit_spread', 'follow:production'))
        self.assertEqual((row['source_account'], row['source_order_id'], row['source_option_symbol']),
                         ('production', 'prod-1', 'XYZ261113C00105000'))
        self.assertEqual(row['legs'], [
            {'symbol': 'XYZ261113C00105000', 'side': 'buy', 'asset': 'option', 'ratio_qty': 1, 'expiration_date': '2026-11-13'},
            {'symbol': 'XYZ261113C00110000', 'side': 'sell', 'asset': 'option', 'ratio_qty': 1, 'expiration_date': '2026-11-13'},
        ])
        self.assertEqual((row['status'], row['qty'], row['multiplier']), ('pending_entry', 4, 100))

    def test_lifecycle(self):
        row_id = record(SPREADS, SPREAD)
        store.mark_entry_order(row_id, order_id='paper-1')
        store.mark_entry_fill(row_id, fill_price=1.0, filled_at='2026-10-28T15:00:00+00:00')
        store.mark_source_seen(row_id, at='2026-10-28T15:05:00+00:00')
        [row] = store.active_positions()
        self.assertEqual((row['status'], row['entry_order_id'], row['entry_fill_price']), ('open', 'paper-1', 1.0))
        self.assertEqual(row['source_seen_at'], '2026-10-28T15:05:00+00:00')

        store.mark_closing(row_id, order_id='paper-2', reason='production_exit')
        self.assertEqual(store.active_positions()[0]['status'], 'closing')
        store.reopen(row_id, error='rejected')
        self.assertEqual(store.active_positions()[0]['status'], 'open')
        store.mark_closing(row_id, order_id='paper-3', reason='production_exit')
        store.mark_closed(row_id, fill_price=1.5, closed_at='2026-11-02T15:00:00+00:00')
        self.assertEqual(store.active_positions(), [])

    def test_skips_and_failures_are_not_active(self):
        record(LONGER, Skip('longer_dte', 'no_longer_bucket'))
        store.mark_failed(record(SHARES, SHORT_SHARES), error='insufficient buying power')
        self.assertEqual(store.active_positions(), [])

    def test_summary_is_keyed_by_account_and_compares_production_on_the_same_entries(self):
        close(record(SPREADS, SPREAD), entry=1.0, exit_=1.5)  # +$200 on $400 = +50%
        close(record(SHARES, SHORT_SHARES), entry=100.0, exit_=105.0)  # short: -$20 on $400 = -5%
        record(LONGER, Skip('longer_dte', 'no_longer_bucket'))
        with closing(get_connection()) as conn, conn:
            conn.executemany('''INSERT INTO manager_decision_history (decision_run_at, trade_execution_order_id, realized_pnl_pct)
                VALUES ('2026-10-28T14:00:00+00:00', ?, ?)''', [('prod-1', 30.0), ('prod-unmirrored', -80.0)])

        summary = store.account_summary()
        spreads = summary['spreads']
        self.assertEqual((spreads['strategy'], spreads['role']), ('debit_spread', 'follower'))
        self.assertEqual((spreads['closed'], spreads['wins']), (1, 1))
        self.assertAlmostEqual(spreads['pnl'], 200.0)
        self.assertAlmostEqual(spreads['capital'], 400.0)
        self.assertAlmostEqual(spreads['avg_pnl_pct'], 50.0)
        self.assertAlmostEqual(spreads['production_avg_pnl_pct'], 30.0)
        self.assertAlmostEqual(summary['shares']['pnl'], -20.0)
        self.assertAlmostEqual(summary['shares']['avg_pnl_pct'], -5.0)
        self.assertEqual(summary['shares']['wins'], 0)
        self.assertEqual(summary['longer_dte']['skipped'], {'no_longer_bucket': 1})
        self.assertEqual(summary['longer_dte']['closed'], 0)
        # Production is summarised only over entries the followers mirrored.
        production = summary['production']
        self.assertEqual((production['role'], production['strategy'], production['closed']), ('primary', 'single_leg', 1))
        self.assertAlmostEqual(production['avg_pnl_pct'], 30.0)


if __name__ == '__main__':
    unittest.main()
