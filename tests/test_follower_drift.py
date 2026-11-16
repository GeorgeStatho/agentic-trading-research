"""Spec for followers that enter on the shares_drift rule and exit by their own policy, not production's."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import logging
import unittest

from db_common import get_connection
from market_db_modules import account_positions as store
from services.core.settings import FollowerSettings
from services.trading.follower_fleet import FollowerFleet
from services.trading.trading_accounts import TradingAccount
from tests import test_follower_fleet as base

DRIFT = 'shares_drift'


def account(name, strategy, policy, entry=DRIFT):
    return TradingAccount(name, f'key-{name}', f'secret-{name}', 'follower', strategy, policy, entry=entry)


class DriftCase(base.FleetTestCase):
    followers = (account('drift_shares', 'shares', 'hold_window'),
                 account('drift_itm', 'itm_single', 'hold_window'),
                 account('drift_spreads', 'debit_spread', 'hold_to_expiry'),
                 account('copy_shares', 'shares', 'follow:production', entry='production'))

    def setUp(self):
        super().setUp()
        self.snapshot = {'option': {'mid_price': None}, 'underlying': {'price': 100.0, 'mid_price': 100.0}}
        for gateway in self.gateways.values():
            gateway.get_execution_market_snapshot.side_effect = lambda **_: self.snapshot
        self.market_open = True
        self.gateways['production'].market_is_open.side_effect = lambda client: self.market_open
        self.fleet = FollowerFleet(
            accounts=[base.PRIMARY, *self.followers],
            settings=FollowerSettings(spread_width_pct=0.05, itm_min_delta=0.80, force_exit_dte=1, max_hold_days=30,
                                      option_min_dte=14, hold_days=5, drift_budget=1000),
            gateway_factory=lambda acct: self.gateways[acct.name], chain_fetcher=self.chain_fetcher,
            logger=logging.getLogger('test_follower_drift'), clock=lambda: self.now)
        self.count = 0

    def candidate(self, catalyst='catalyst:a', *, family='earnings_positive', direction='positive', age_hours=2.0,
                  created_ago=timedelta(minutes=10), prefix='candidate'):
        """A recorded candidate whose primary catalyst was first seen `age_hours` before the candidate."""
        self.count += 1
        created = self.now - created_ago
        state = {'catalyst_id': catalyst, 'catalyst_family': family, 'direction': direction,
                 'first_seen_time': (created - timedelta(hours=age_hours)).isoformat()}
        candidate_id = f'{prefix}-{self.count}'
        with closing(get_connection()) as conn, conn:
            conn.execute('''INSERT OR IGNORE INTO catalysts (catalyst_id, ticker, catalyst_type, catalyst_subtype,
                catalyst_family, catalyst_scope, direction, mechanism, expected_horizon, article_count, source_count,
                independent_root_count, primary_source_present, classification_version, classification_status,
                state_json, updated_at) VALUES (?, 'XYZ', 'earnings', 'x', ?, 'idiosyncratic', ?, 'm', '4_10_days',
                1, 1, 1, 0, 'v', 'classified', '{}', ?)''', (catalyst, family, direction, created.isoformat()))
            conn.execute("INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, "
                         "pipeline_version_json) VALUES (?, 'XYZ', ?, '[]', '[]', '{}')", (candidate_id, created.isoformat()))
            conn.execute('INSERT INTO candidate_catalysts VALUES (?, ?, ?, 1, ?)',
                         (candidate_id, catalyst, family, json.dumps(state)))
        return candidate_id

    def statuses(self):
        return {name: (row['status'], row['exit_reason']) for name, row in self.rows().items()}


class DriftEntryTests(DriftCase):
    def test_a_fresh_earnings_catalyst_enters_every_drift_account_once_and_no_copy_account(self):
        self.candidate()
        self.fleet.enter_drift()
        self.gateways['drift_shares'].submit_stock_market_order.assert_called_once_with(
            'client-drift_shares', symbol='XYZ', qty=10, side='buy')
        # Deep in the money: the only call with delta >= 0.80 at the first expiry 14+ days out.
        self.gateways['drift_itm'].submit_option_market_order.assert_called_once_with(
            'client-drift_itm', option_symbol='XYZ261113C00090000', qty=1)
        # No production contract: the long leg is the at-the-money call, the short one width beyond.
        self.gateways['drift_spreads'].submit_multileg_market_order.assert_called_once_with(
            'client-drift_spreads', legs=[{'symbol': 'XYZ261113C00100000', 'side': 'buy', 'ratio_qty': 1},
                                          {'symbol': 'XYZ261113C00105000', 'side': 'sell', 'ratio_qty': 1}],
            qty=4, opening=True)
        self.gateways['copy_shares'].submit_stock_market_order.assert_not_called()
        rows = self.rows()
        self.assertEqual({row['entry_kind'] for row in rows.values()}, {DRIFT})
        self.assertEqual({row['source_order_id'] for row in rows.values()}, {'drift:catalyst:a'})

        self.candidate()  # a repeat candidate on the same catalyst
        self.fleet.enter_drift()
        self.assertEqual(self.gateways['drift_shares'].submit_stock_market_order.call_count, 1)

    def test_a_later_candidate_that_learned_the_direction_still_enters(self):
        self.candidate(direction='unknown', created_ago=timedelta(minutes=30))
        self.candidate(created_ago=timedelta(minutes=5))
        self.fleet.enter_drift()
        self.assertEqual(self.gateways['drift_shares'].submit_stock_market_order.call_count, 1)

    def test_a_negative_analyst_catalyst_shorts_the_shares(self):
        self.candidate(family='analyst', direction='negative')
        self.fleet.enter_drift()
        self.gateways['drift_shares'].submit_stock_market_order.assert_called_once_with(
            'client-drift_shares', symbol='XYZ', qty=10, side='sell')

    def test_candidates_outside_the_rule_never_enter(self):
        self.candidate('catalyst:stale', age_hours=24)
        self.candidate('catalyst:family', family='product_commercial')
        self.candidate('catalyst:direction', direction='unknown')
        self.candidate('catalyst:old', created_ago=timedelta(hours=25))
        self.candidate('catalyst:research', prefix='discovery:x')
        self.fleet.enter_drift()
        self.assertEqual(store.active_positions(), [])
        self.chain_fetcher.assert_not_called()

    def test_a_closed_market_waits_for_the_open(self):
        self.candidate()
        self.market_open = False
        self.fleet.enter_drift()
        self.assertEqual(store.active_positions(), [])
        self.market_open = True
        self.fleet.enter_drift()
        self.assertEqual(len(store.active_positions()), 3)

    def test_production_entries_are_not_copied_into_drift_accounts(self):
        self.fleet.mirror_entry(base.candidate(), base.execution())
        self.assertEqual(list(self.rows()), ['copy_shares'])


class OwnExitTests(DriftCase):
    def setUp(self):
        super().setUp()
        self.candidate()
        self.fleet.enter_drift()
        self.fill_all({'drift_shares': 100.0, 'drift_itm': 11.2, 'drift_spreads': 1.5})
        self.fleet.manage()

    def test_production_exiting_closes_nothing(self):
        self.production_holdings = []
        self.fleet.manage()
        self.assertEqual({status for status, _ in self.statuses().values()}, {'open'})

    def test_hold_window_closes_after_the_holding_days_and_the_spread_holds_on(self):
        self.now += timedelta(days=5, minutes=1)  # the fills landed 30 seconds after the entry tick
        self.fleet.manage()
        statuses = self.statuses()
        self.assertEqual((statuses['drift_shares'], statuses['drift_itm']),
                         (('closing', 'hold_window'), ('closing', 'hold_window')))
        self.assertEqual(statuses['drift_spreads'], ('open', None))
        self.gateways['drift_shares'].submit_stock_market_order.assert_called_with(
            'client-drift_shares', symbol='XYZ', qty=10, side='sell')

    def test_no_own_exit_stops_on_an_adverse_stock_move(self):
        # The shares_drift research strategy holds the window with no stop, so the live accounts do too.
        self.snapshot['underlying'] = {'price': 90.0, 'mid_price': 90.0}
        self.fleet.manage()
        self.assertEqual({status for status, _ in self.statuses().values()}, {'open'})

    def test_hold_to_expiry_closes_at_the_expiry_guard(self):
        self.now = datetime(2026, 11, 12, 15, tzinfo=timezone.utc)  # one day before the 2026-11-13 expiry
        self.fleet.manage()
        self.assertEqual(self.statuses()['drift_spreads'], ('closing', 'expiry_guard'))


if __name__ == '__main__':
    unittest.main()
