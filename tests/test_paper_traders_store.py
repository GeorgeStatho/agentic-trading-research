from __future__ import annotations

import unittest
from contextlib import closing

from db_common import get_connection
from market_db_modules import paper_traders as store
from services.paper_strategies import PaperLeg, PaperOrderPlan, Skip
from tests.db_fixtures import temporary_database

SPREAD = PaperOrderPlan('debit_spread', (PaperLeg('XYZ261113C00105000', 'buy', 'option', 1, '2026-11-13'),
                                         PaperLeg('XYZ261113C00110000', 'sell', 'option', 1, '2026-11-13')),
                        qty=4, unit_cost=100.0, multiplier=100)
SHORT_SHARES = PaperOrderPlan('shares', (PaperLeg('XYZ', 'sell', 'stock'),), qty=4, unit_cost=100.0, multiplier=1)


def record(plan, *, trader=None, production_order_id='prod-1'):
    return store.record_plan(trader=trader or plan.trader, account=trader or plan.trader,
                             production_order_id=production_order_id,
                             production_option_symbol='XYZ261113C00105000', underlying='XYZ', decision='call',
                             budget=400.0, plan=plan)


class PaperTraderStoreTests(unittest.TestCase):
    def setUp(self):
        temporary_database(self)

    def test_plan_is_recorded_once_per_trader_and_production_entry(self):
        row_id = record(SPREAD)
        self.assertIsInstance(row_id, int)
        self.assertIsNone(record(SPREAD))
        self.assertIsInstance(record(SPREAD, production_order_id='prod-2'), int)

    def test_active_positions_decode_legs_and_track_lifecycle(self):
        row_id = record(SPREAD)
        [row] = store.active_positions()
        self.assertEqual(row['status'], 'pending_entry')
        self.assertEqual(row['legs'], [
            {'symbol': 'XYZ261113C00105000', 'side': 'buy', 'asset': 'option', 'ratio_qty': 1, 'expiration_date': '2026-11-13'},
            {'symbol': 'XYZ261113C00110000', 'side': 'sell', 'asset': 'option', 'ratio_qty': 1, 'expiration_date': '2026-11-13'},
        ])
        self.assertEqual((row['qty'], row['multiplier'], row['production_order_id']), (4, 100, 'prod-1'))

        store.mark_entry_order(row_id, order_id='paper-1')
        store.mark_entry_fill(row_id, fill_price=1.0, filled_at='2026-10-28T15:00:00+00:00')
        store.mark_production_seen(row_id, at='2026-10-28T15:05:00+00:00')
        [row] = store.active_positions()
        self.assertEqual((row['status'], row['entry_order_id'], row['entry_fill_price']), ('open', 'paper-1', 1.0))
        self.assertEqual(row['production_seen_at'], '2026-10-28T15:05:00+00:00')

        store.mark_closing(row_id, order_id='paper-2', reason='production_exit')
        self.assertEqual(store.active_positions()[0]['status'], 'closing')
        store.reopen(row_id, error='rejected')
        self.assertEqual(store.active_positions()[0]['status'], 'open')
        store.mark_closing(row_id, order_id='paper-3', reason='production_exit')
        store.mark_closed(row_id, fill_price=1.5, closed_at='2026-11-02T15:00:00+00:00')
        self.assertEqual(store.active_positions(), [])

    def test_skips_and_failures_are_not_active(self):
        store.record_plan(trader='longer_dte', account='longer_dte', production_order_id='prod-1',
                          production_option_symbol='XYZ261113C00105000', underlying='XYZ', decision='call',
                          budget=400.0, plan=Skip('longer_dte', 'no_longer_bucket'))
        failed = record(SHORT_SHARES)
        store.mark_failed(failed, error='insufficient buying power')
        self.assertEqual(store.active_positions(), [])

    def test_summary_computes_pnl_per_trader_and_compares_production(self):
        # Spread: debit 1.00 -> credit 1.50 on 4 spreads = +$200 on $400 = +50%.
        spread = record(SPREAD)
        store.mark_entry_fill(spread, fill_price=1.0, filled_at='2026-10-28T15:00:00+00:00')
        store.mark_closing(spread, order_id='x', reason='production_exit')
        store.mark_closed(spread, fill_price=1.5, closed_at='2026-11-02T15:00:00+00:00')
        # Short shares: 100 -> 105 on 4 shares = -$20 on $400 = -5%.
        short = record(SHORT_SHARES)
        store.mark_entry_fill(short, fill_price=100.0, filled_at='2026-10-28T15:00:00+00:00')
        store.mark_closing(short, order_id='y', reason='production_exit')
        store.mark_closed(short, fill_price=105.0, closed_at='2026-11-02T15:00:00+00:00')
        store.record_plan(trader='longer_dte', account='longer_dte', production_order_id='prod-1',
                          production_option_symbol='XYZ261113C00105000', underlying='XYZ', decision='call',
                          budget=400.0, plan=Skip('longer_dte', 'no_longer_bucket'))
        with closing(get_connection()) as conn, conn:
            conn.execute('''INSERT INTO manager_decision_history (decision_run_at, trade_execution_order_id, realized_pnl_pct)
                VALUES ('2026-10-28T14:00:00+00:00', 'prod-1', 30.0)''')

        summary = store.trader_summary()
        self.assertEqual(summary['debit_spread']['closed'], 1)
        self.assertEqual(summary['debit_spread']['wins'], 1)
        self.assertAlmostEqual(summary['debit_spread']['pnl'], 200.0)
        self.assertAlmostEqual(summary['debit_spread']['capital'], 400.0)
        self.assertAlmostEqual(summary['debit_spread']['avg_pnl_pct'], 50.0)
        self.assertAlmostEqual(summary['debit_spread']['production_avg_pnl_pct'], 30.0)
        self.assertAlmostEqual(summary['shares']['pnl'], -20.0)
        self.assertAlmostEqual(summary['shares']['avg_pnl_pct'], -5.0)
        self.assertEqual(summary['shares']['wins'], 0)
        self.assertEqual(summary['longer_dte']['skipped'], {'no_longer_bucket': 1})
        self.assertEqual(summary['longer_dte']['closed'], 0)


if __name__ == '__main__':
    unittest.main()
