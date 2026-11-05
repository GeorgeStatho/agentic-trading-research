"""The hybrid selector on the thin 30-45 DTE chains that left BX and BLK calls without a contract.

Weekly rows are copied from the stored selector debug of the Oct 29 BX and BLK decisions. The Dec 18
monthly rows are live indicative quotes and open interest taken on Oct 29 during market hours.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import agent_helpers.deterministic_option_selector as selector
from agent_helpers.deterministic_option_selector.market import selection_date

DECISION_TIME = datetime(2026, 10, 29, 15, 10, tzinfo=timezone.utc)


def contract(option_id, symbol, expiration, strike, open_interest, bid, ask, delta, gamma, theta, vega, iv):
    return {
        "option_id": option_id,
        "symbol": symbol,
        "contract_type": "call",
        "expiration_date": expiration,
        "strike_price": strike,
        "open_interest": open_interest,
        "implied_volatility": iv,
        "latest_quote": {"bid_price": bid, "ask_price": ask, "midpoint_price": round((bid + ask) / 2, 4)},
        "greeks": {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega},
    }


# BX decision 2984, spot 120.27: the seven weeklies the selector was offered.
BX_WEEKLIES = [
    contract(1, "BX261204C00122000", "2026-12-04", 122.0, 1.0, 3.83, 4.36, 0.4735, 0.0336, -0.0712, 0.1502, 0.3141),
    contract(2, "BX261211C00122000", "2026-12-11", 122.0, None, 3.82, 5.40, 0.4825, 0.0306, -0.0662, 0.1644, 0.3163),
    contract(3, "BX261204C00123000", "2026-12-04", 123.0, 1.0, 3.20, 3.93, 0.4382, 0.0341, -0.0686, 0.1487, 0.3065),
    contract(4, "BX261211C00123000", "2026-12-11", 123.0, None, 3.42, 4.97, 0.4527, 0.0303, -0.0656, 0.1634, 0.3167),
    contract(5, "BX261204C00124000", "2026-12-04", 124.0, 1.0, 3.03, 3.68, 0.4102, 0.0324, -0.0698, 0.1467, 0.3183),
    contract(6, "BX261211C00124000", "2026-12-11", 124.0, None, 3.25, 4.37, 0.4236, 0.0299, -0.0646, 0.1615, 0.3173),
    contract(7, "BX261204C00125000", "2026-12-04", 125.0, 7.0, 2.43, 3.29, 0.3744, 0.0326, -0.0658, 0.1430, 0.3086),
]
# The liquid Dec 18 monthly, 50 DTE: just past the 30-45 window.
BX_MONTHLY = [
    contract(8, "BX261218C00125000", "2026-12-18", 125.0, 1730.0, 3.77, 4.03, 0.4059, 0.0271, -0.0604, 0.1722, 0.3226),
]

# BLK decision 2983, spot 1170.69: the four weeklies the selector was offered.
BLK_WEEKLIES = [
    contract(1, "BLK261204C01180000", "2026-12-04", 1180.0, None, 31.83, 39.42, 0.4971, 0.0042, -0.5831, 1.4670, 0.2575),
    contract(2, "BLK261211C01180000", "2026-12-11", 1180.0, None, 32.74, 40.59, 0.5014, 0.0041, -0.5056, 1.6034, 0.2395),
    contract(3, "BLK261204C01190000", "2026-12-04", 1190.0, None, 27.30, 34.38, 0.4549, 0.0042, -0.5700, 1.4577, 0.2550),
    contract(4, "BLK261211C01190000", "2026-12-11", 1190.0, None, 28.15, 35.68, 0.4600, 0.0042, -0.4953, 1.5953, 0.2377),
]
# BLK's Dec 18 monthly: 1180 has too little open interest and 1200 is quoted 12.2% wide.
BLK_MONTHLY = [
    contract(5, "BLK261218C01180000", "2026-12-18", 1180.0, 59.0, 39.09, 41.06, 0.5026, 0.0038, -0.4792, 1.7269, 0.2433),
    contract(6, "BLK261218C01200000", "2026-12-18", 1200.0, 1526.0, 29.15, 32.94, 0.4273, 0.0038, -0.4589, 1.6982, 0.2405),
]


def manager_result(spot, contracts, *, bucket="30_45", hv_20d=0.25):
    return {
        "recommendation": {"decision": "call", "confidence": "high", "target_dte_bucket": bucket, "reason": "test"},
        "strategist_recommendation": {"decision": "trade_candidate"},
        "entry_gate": {"allowed": True},
        "market_context": {
            "current_stock_price": {"price": spot},
            "underlying_price_history": {"available": True, "historical_volatility_20d": hv_20d,
                                         "historical_volatility_60d": hv_20d},
            "option_market": {"contracts": [dict(c) for c in contracts], "selection_filters": {},
                              "volatility_summary": {"term_structure": {"event_risk_flag": False}}},
        },
    }


def select(spot, contracts, **kwargs):
    with selection_date(DECISION_TIME):
        return selector.apply_deterministic_option_selection(manager_result(spot, contracts, **kwargs))


def rejection_reasons(result):
    examples = result["recommendation"]["selection_debug"]["selector_debug"].get("rejected_hybrid_examples", [])
    return {example["symbol"]: example["rejection_reasons"] for example in examples}


# Production settings: hybrid with no simple fallback, so a hybrid rejection is final.
@patch.object(selector, "OPTION_SELECTOR_MODE", "hybrid")
@patch.object(selector, "ALLOW_RISKY_SIMPLE_FALLBACK", False)
@patch.dict(os.environ, {"V2_LIQUIDITY_MODE": "shadow", "V2_SCENARIO_MODE": "shadow"})
class ThinChainSelectionTests(unittest.TestCase):
    def test_blk_weeklies_as_offered_still_select_nothing(self):
        # Missing open interest and 21-24% spreads: the liquidity checks must keep rejecting these.
        result = select(1170.69, BLK_WEEKLIES)

        self.assertIsNone(result["selected_option"])
        reasons = rejection_reasons(result)
        self.assertIn("spread_above_max", reasons["BLK261204C01190000"])

    def test_bx_monthly_just_past_the_bucket_is_selected(self):
        result = select(120.27, BX_WEEKLIES + BX_MONTHLY)

        self.assertIsNotNone(result["selected_option"])
        self.assertEqual(result["selected_option"]["symbol"], "BX261218C00125000")

    def test_blk_monthly_is_still_held_to_open_interest_and_spread(self):
        result = select(1170.69, BLK_WEEKLIES + BLK_MONTHLY)

        self.assertIsNone(result["selected_option"])
        reasons = rejection_reasons(result)
        self.assertIn("open_interest_below_min", reasons["BLK261218C01180000"])
        self.assertIn("spread_above_max", reasons["BLK261218C01200000"])

    def test_liquid_strike_nearer_than_the_otm_target_is_eligible(self):
        # Target distance is 2.5% of 120.27 = $3.01; this strike is $0.73 out of the money.
        near = contract(1, "BX261204C00121000", "2026-12-04", 121.0, 500.0, 4.20, 4.40,
                        0.47, 0.033, -0.070, 0.150, 0.31)

        result = select(120.27, [near])

        self.assertIsNotNone(result["selected_option"])
        self.assertEqual(result["selected_option"]["symbol"], "BX261204C00121000")

    def test_strike_closer_to_the_otm_target_still_ranks_first(self):
        near = contract(1, "BX261204C00121000", "2026-12-04", 121.0, 500.0, 4.20, 4.40,
                        0.47, 0.033, -0.070, 0.150, 0.31)
        at_target = contract(2, "BX261204C00124000", "2026-12-04", 124.0, 500.0, 2.90, 3.04,
                             0.40, 0.033, -0.068, 0.148, 0.31)

        result = select(120.27, [near, at_target])

        self.assertEqual(result["selected_option"]["symbol"], "BX261204C00124000")

    def test_gamma_floor_does_not_depend_on_share_price(self):
        # The same contract shape at $120 and at $1,170: gamma scales with 1/spot.
        cheap = contract(1, "CHEAP261204C00123000", "2026-12-04", 123.0, 500.0, 3.40, 3.56,
                         0.44, 0.042, -0.070, 0.150, 0.28)
        dear = contract(1, "DEAR261204C01180000", "2026-12-04", 1180.0, 500.0, 33.10, 34.70,
                        0.44, 0.0043, -0.680, 1.460, 0.28)

        cheap_result = select(120.0, [cheap], hv_20d=0.28)
        dear_result = select(1170.0, [dear], hv_20d=0.28)

        self.assertIsNotNone(cheap_result["selected_option"])
        self.assertIsNotNone(dear_result["selected_option"], rejection_reasons(dear_result))

    def test_in_window_contract_with_thin_open_interest_or_wide_spread_is_rejected(self):
        thin = contract(1, "BX261204C00123000", "2026-12-04", 123.0, 5.0, 3.40, 3.56,
                        0.44, 0.033, -0.070, 0.150, 0.31)
        wide = contract(2, "BX261204C00124000", "2026-12-04", 124.0, 500.0, 2.70, 3.30,
                        0.40, 0.033, -0.068, 0.148, 0.31)

        result = select(120.27, [thin, wide])

        self.assertIsNone(result["selected_option"])
        reasons = rejection_reasons(result)
        self.assertIn("open_interest_below_min", reasons["BX261204C00123000"])
        self.assertIn("spread_above_max", reasons["BX261204C00124000"])


if __name__ == "__main__":
    unittest.main()
