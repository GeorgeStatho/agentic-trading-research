"""Spec for each follower strategy's order, from the plan's strike and quantity rules.

Fixture: underlying XYZ at spot 100 with hand-set quotes in tests/fixtures/follower_chain.json.
Production bought the 2026-11-13 105 call (ask 1.55) or the 2026-11-13 95 put (ask 1.30).
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from services.trading.account_strategies import OrderLeg, OrderPlan, Skip, build_order
from services.trading.trading_accounts import FollowerSettings

CHAIN = json.loads((Path(__file__).parent / 'fixtures' / 'follower_chain.json').read_text())
SETTINGS = FollowerSettings(spread_width_pct=0.05, itm_min_delta=0.70, force_exit_dte=1, max_hold_days=30)


def contract(symbol, chains=CHAIN):
    for members in chains['buckets'].values():
        for item in members:
            if item['symbol'] == symbol:
                return copy.deepcopy(item)
    raise KeyError(symbol)


PRODUCTION_CALL = contract('XYZ261113C00105000')
PRODUCTION_PUT = contract('XYZ261113P00095000')


def build(strategy, decision='call', *, production=None, chains=CHAIN, spot=100.0, budget=400.0, settings=SETTINGS):
    if production is None:
        production = PRODUCTION_CALL if decision == 'call' else PRODUCTION_PUT
    return build_order(strategy, decision=decision, underlying='XYZ', production_contract=production,
                       chains=chains, spot=spot, budget=budget, settings=settings)


class DebitSpreadTests(unittest.TestCase):
    def test_call_spread_buys_production_call_and_sells_nearest_strike_one_width_beyond(self):
        # Width is 5% of spot = 5, so the short strike is the first call strike >= 110.
        plan = build('debit_spread', 'call')
        self.assertIsInstance(plan, OrderPlan)
        self.assertEqual(plan.legs, (
            OrderLeg('XYZ261113C00105000', 'buy', 'option', 1, '2026-11-13'),
            OrderLeg('XYZ261113C00110000', 'sell', 'option', 1, '2026-11-13'),
        ))
        # Net debit = long ask 1.55 - short bid 0.55 = 1.00 -> $100 per spread; $400 buys 4.
        self.assertAlmostEqual(plan.unit_cost, 100.0)
        self.assertEqual(plan.qty, 4)
        self.assertEqual(plan.multiplier, 100)
        self.assertAlmostEqual(plan.capital, 400.0)

    def test_put_spread_mirrors_downward(self):
        plan = build('debit_spread', 'put')
        self.assertEqual([leg.symbol for leg in plan.legs], ['XYZ261113P00095000', 'XYZ261113P00090000'])
        self.assertEqual([leg.side for leg in plan.legs], ['buy', 'sell'])
        # 1.30 - 0.40 = 0.90 -> $90 per spread; floor(400 / 90) = 4.
        self.assertAlmostEqual(plan.unit_cost, 90.0)
        self.assertEqual(plan.qty, 4)

    def test_buys_one_spread_when_budget_is_below_one_unit(self):
        self.assertEqual(build('debit_spread', budget=50.0).qty, 1)

    def test_skips_when_short_leg_has_no_bid(self):
        chains = copy.deepcopy(CHAIN)
        for item in chains['buckets']['12_20']:
            if item['symbol'] == 'XYZ261113C00110000':
                item['latest_quote']['bid_price'] = 0.0
        self.assertEqual(build('debit_spread', chains=chains), Skip('debit_spread', 'no_short_bid'))

    def test_skips_when_no_strike_is_a_full_width_beyond(self):
        wide = FollowerSettings(spread_width_pct=0.20)
        self.assertEqual(build('debit_spread', settings=wide), Skip('debit_spread', 'no_short_strike'))


class ItmSingleTests(unittest.TestCase):
    def test_call_picks_the_itm_strike_nearest_spot_that_meets_the_delta_floor(self):
        # ITM calls: 90 (delta .88) and 95 (.74); both clear .70, and 95 is nearer spot.
        plan = build('itm_single', 'call', budget=1500.0)
        self.assertEqual(plan.legs, (OrderLeg('XYZ261113C00095000', 'buy', 'option', 1, '2026-11-13'),))
        self.assertAlmostEqual(plan.unit_cost, 700.0)
        self.assertEqual(plan.qty, 2)
        self.assertEqual(plan.multiplier, 100)

    def test_put_picks_strike_above_spot(self):
        plan = build('itm_single', 'put', budget=1500.0)
        self.assertEqual(plan.legs[0].symbol, 'XYZ261113P00105000')
        self.assertAlmostEqual(plan.unit_cost, 680.0)
        self.assertEqual(plan.qty, 2)

    def test_buys_one_contract_even_when_it_exceeds_the_budget(self):
        self.assertEqual(build('itm_single', budget=400.0).qty, 1)

    def test_skips_when_no_itm_strike_meets_the_delta_floor(self):
        strict = FollowerSettings(itm_min_delta=0.90)
        self.assertEqual(build('itm_single', settings=strict), Skip('itm_single', 'no_itm_strike'))

    def test_without_greeks_falls_back_to_the_first_itm_strike(self):
        chains = copy.deepcopy(CHAIN)
        for members in chains['buckets'].values():
            for item in members:
                item['greeks']['delta'] = None
        self.assertEqual(build('itm_single', chains=chains).legs[0].symbol, 'XYZ261113C00095000')


class LongerDteTests(unittest.TestCase):
    """One DTE bucket further out, the same distance OTM as production's contract.

    Production's OTM distance is its strike against spot (105 call at spot 100 = 5% OTM), so the
    target strike is spot x (1 + 5%); the nearest listed strike wins, ties going further OTM.
    """

    def test_call_moves_one_bucket_out_at_the_same_otm_distance(self):
        # Production is in 12_20, so use 20_30: earliest expiry 2026-11-20; 5% OTM of spot 100 = 105.
        plan = build('longer_dte', 'call', budget=1000.0)
        self.assertEqual(plan.legs, (OrderLeg('XYZ261120C00105000', 'buy', 'option', 1, '2026-11-20'),))
        self.assertAlmostEqual(plan.unit_cost, 225.0)
        self.assertEqual(plan.qty, 4)

    def test_put_keeps_its_otm_distance_below_spot(self):
        # The 95 put is 5% OTM, so the target is the 95 put in 20_30.
        plan = build('longer_dte', 'put')
        self.assertEqual(plan.legs[0].symbol, 'XYZ261120P00095000')
        self.assertEqual(plan.qty, 2)  # floor(400 / 195)

    def test_contract_missing_from_chain_uses_its_dte_and_breaks_strike_ties_further_otm(self):
        # 15 DTE puts it in 12_20. 102.5 is 2.5% OTM; the 20_30 strikes 100 and 105 are equally far
        # from that target, so take the further-OTM 105.
        production = {**PRODUCTION_CALL, 'symbol': 'XYZ261113C00102500', 'strike_price': 102.5, 'days_to_expiration': 15}
        self.assertEqual(build('longer_dte', production=production).legs[0].symbol, 'XYZ261120C00105000')

    def test_skips_when_production_is_already_in_the_last_bucket(self):
        production = contract('XYZ270115C00105000')
        self.assertEqual(build('longer_dte', production=production), Skip('longer_dte', 'no_longer_bucket'))


class SharesTests(unittest.TestCase):
    def test_call_buys_and_put_shorts_the_same_dollar_budget(self):
        long = build('shares', 'call')
        self.assertEqual(long.legs, (OrderLeg('XYZ', 'buy', 'stock', 1, None),))
        self.assertEqual(long.qty, 4)
        self.assertAlmostEqual(long.unit_cost, 100.0)
        self.assertEqual(long.multiplier, 1)
        short = build('shares', 'put')
        self.assertEqual(short.legs, (OrderLeg('XYZ', 'sell', 'stock', 1, None),))
        self.assertEqual(short.qty, 4)

    def test_buys_one_share_when_budget_is_below_spot(self):
        self.assertEqual(build('shares', budget=50.0).qty, 1)

    def test_does_not_need_an_option_chain(self):
        self.assertIsInstance(build('shares', chains={'available': False, 'buckets': {}}), OrderPlan)


class CommonRulesTests(unittest.TestCase):
    def test_non_directional_decision_is_skipped_by_every_strategy(self):
        for strategy in ('debit_spread', 'itm_single', 'longer_dte', 'shares'):
            self.assertEqual(build(strategy, 'neither', production=PRODUCTION_CALL), Skip(strategy, 'not_directional'))

    def test_option_strategies_skip_when_chain_is_unavailable(self):
        for strategy in ('debit_spread', 'itm_single', 'longer_dte'):
            self.assertEqual(build(strategy, chains={'available': False, 'buckets': {}}), Skip(strategy, 'chain_unavailable'))

    def test_missing_budget_is_skipped(self):
        self.assertEqual(build('shares', budget=None), Skip('shares', 'no_budget'))

    def test_unknown_strategy_is_an_error(self):
        with self.assertRaises(ValueError):
            build('iron_condor')


if __name__ == '__main__':
    unittest.main()
