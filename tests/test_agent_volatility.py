from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from agent_helpers.volatility import (
    compute_percentile_rank,
    compute_realized_volatility,
    summarize_iv_term_structure,
    summarize_iv_vs_hv,
    summarize_option_iv,
)


class AgentVolatilityTests(unittest.TestCase):
    @staticmethod
    def _future_date(days_from_today: int) -> str:
        return (date.today() + timedelta(days=days_from_today)).isoformat()

    def test_compute_realized_volatility_returns_annualized_decimal_ratio(self):
        closes = [
            100.0,
            101.0,
            99.5,
            102.0,
            101.25,
            103.5,
            102.75,
            104.25,
            103.0,
            105.0,
        ]

        result = compute_realized_volatility(closes, 10)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertGreater(result, 0.0)
        self.assertLess(result, 1.0)

    def test_compute_realized_volatility_requires_enough_valid_closes(self):
        closes = [100.0, None, "bad", 101.0]

        result = compute_realized_volatility(closes, 5)

        self.assertIsNone(result)

    def test_compute_percentile_rank_returns_expected_percentile(self):
        result = compute_percentile_rank(0.55, [0.30, 0.40, 0.50, 0.60, 0.70])

        self.assertEqual(result, 60.0)

    def test_summarize_option_iv_returns_atm_call_and_put_summary(self):
        contracts = [
            {
                "symbol": "AAPL260619C00195000",
                "contract_type": "call",
                "strike_price": 195.0,
                "expiration_date": "2026-06-19",
                "implied_volatility": 0.29,
            },
            {
                "symbol": "AAPL260619C00200000",
                "contract_type": "call",
                "strike_price": 200.0,
                "expiration_date": "2026-06-19",
                "implied_volatility": 0.31,
            },
            {
                "symbol": "AAPL260619P00195000",
                "contract_type": "put",
                "strike_price": 195.0,
                "expiration_date": "2026-06-19",
                "implied_volatility": 0.30,
            },
            {
                "symbol": "AAPL260619P00190000",
                "contract_type": "put",
                "strike_price": 190.0,
                "expiration_date": "2026-06-19",
                "implied_volatility": 0.28,
            },
        ]

        result = summarize_option_iv(contracts, 197.0)

        self.assertTrue(result["available"])
        self.assertEqual(result["contracts_with_iv_count"], 4)
        self.assertEqual(result["atm_call_symbol"], "AAPL260619C00195000")
        self.assertEqual(result["atm_put_symbol"], "AAPL260619P00195000")
        self.assertEqual(result["atm_call_iv"], 0.29)
        self.assertEqual(result["atm_put_iv"], 0.30)
        self.assertEqual(result["atm_mean_iv"], 0.295)

    def test_summarize_option_iv_handles_missing_iv_data(self):
        contracts = [
            {
                "symbol": "AAPL260619C00200000",
                "contract_type": "call",
                "strike_price": 200.0,
                "expiration_date": "2026-06-19",
                "implied_volatility": None,
            }
        ]

        result = summarize_option_iv(contracts, 197.0)

        self.assertFalse(result["available"])
        self.assertEqual(result["contracts_with_iv_count"], 0)
        self.assertIsNone(result["atm_call_iv"])
        self.assertIsNone(result["mean_implied_volatility"])
        self.assertFalse(result["atm_iv_vs_hv"]["available"])

    def test_summarize_iv_vs_hv_classifies_expensive_options(self):
        result = summarize_iv_vs_hv(0.70, 0.35)

        self.assertTrue(result["available"])
        self.assertEqual(result["iv_hv_ratio"], 2.0)
        self.assertEqual(result["pricing_band"], "expensive")
        self.assertEqual(result["label"], "Expensive, reduce confidence")

    def test_summarize_iv_vs_hv_classifies_very_expensive_options(self):
        result = summarize_iv_vs_hv(0.84, 0.35)

        self.assertTrue(result["available"])
        self.assertEqual(result["iv_hv_ratio"], 2.4)
        self.assertEqual(result["pricing_band"], "very_expensive")
        self.assertEqual(result["label"], "Very expensive, usually avoid buying")

    def test_summarize_iv_vs_hv_classifies_boundary_ratio_as_acceptable(self):
        result = summarize_iv_vs_hv(0.385, 0.35)

        self.assertTrue(result["available"])
        self.assertEqual(result["iv_hv_ratio"], 1.1)
        self.assertEqual(result["pricing_band"], "acceptable")
        self.assertEqual(result["label"], "Acceptable")

    def test_summarize_iv_vs_hv_classifies_sub_1_1_ratio_as_reasonably_priced(self):
        result = summarize_iv_vs_hv(0.3815, 0.35)

        self.assertTrue(result["available"])
        self.assertEqual(result["iv_hv_ratio"], 1.09)
        self.assertEqual(result["pricing_band"], "reasonably_priced")
        self.assertEqual(result["label"], "Options are reasonably priced")

    def test_summarize_iv_term_structure_flags_event_risk_when_front_iv_is_much_higher(self):
        short_expiration = self._future_date(7)
        longer_expiration = self._future_date(30)
        contracts = [
            {
                "symbol": "AAPL260528C00195000",
                "contract_type": "call",
                "strike_price": 195.0,
                "expiration_date": short_expiration,
                "implied_volatility": 0.94,
            },
            {
                "symbol": "AAPL260528P00195000",
                "contract_type": "put",
                "strike_price": 195.0,
                "expiration_date": short_expiration,
                "implied_volatility": 0.96,
            },
            {
                "symbol": "AAPL260620C00195000",
                "contract_type": "call",
                "strike_price": 195.0,
                "expiration_date": longer_expiration,
                "implied_volatility": 0.54,
            },
            {
                "symbol": "AAPL260620P00195000",
                "contract_type": "put",
                "strike_price": 195.0,
                "expiration_date": longer_expiration,
                "implied_volatility": 0.56,
            },
        ]

        result = summarize_iv_term_structure(
            contracts,
            197.0,
            short_term_max_dte=10,
            longer_term_min_dte=20,
            longer_term_max_dte=45,
        )

        self.assertTrue(result["available"])
        self.assertTrue(result["comparison_available"])
        self.assertEqual(result["short_term_selected_dte"], 7)
        self.assertEqual(result["longer_term_selected_dte"], 30)
        self.assertEqual(result["short_term_atm_iv"], 0.95)
        self.assertEqual(result["longer_term_atm_iv"], 0.55)
        self.assertAlmostEqual(result["iv_ratio"], 1.727273, places=6)
        self.assertEqual(result["iv_spread"], 0.4)
        self.assertTrue(result["event_risk_flag"])
        self.assertIn("event pricing", result["warning_message"])
        self.assertEqual(result["curve_shape"], "inverted")

    def test_summarize_iv_term_structure_does_not_flag_normal_curve(self):
        short_expiration = self._future_date(7)
        longer_expiration = self._future_date(30)
        contracts = [
            {
                "symbol": "AAPL260528C00195000",
                "contract_type": "call",
                "strike_price": 195.0,
                "expiration_date": short_expiration,
                "implied_volatility": 0.58,
            },
            {
                "symbol": "AAPL260528P00195000",
                "contract_type": "put",
                "strike_price": 195.0,
                "expiration_date": short_expiration,
                "implied_volatility": 0.60,
            },
            {
                "symbol": "AAPL260620C00195000",
                "contract_type": "call",
                "strike_price": 195.0,
                "expiration_date": longer_expiration,
                "implied_volatility": 0.55,
            },
            {
                "symbol": "AAPL260620P00195000",
                "contract_type": "put",
                "strike_price": 195.0,
                "expiration_date": longer_expiration,
                "implied_volatility": 0.57,
            },
        ]

        result = summarize_iv_term_structure(
            contracts,
            197.0,
            short_term_target_dte=7,
            short_term_max_dte=10,
            longer_term_target_dte=30,
            longer_term_min_dte=20,
            longer_term_max_dte=45,
        )

        self.assertTrue(result["comparison_available"])
        self.assertEqual(result["short_term_atm_iv"], 0.59)
        self.assertEqual(result["longer_term_atm_iv"], 0.56)
        self.assertAlmostEqual(result["iv_ratio"], 1.053571, places=6)
        self.assertEqual(result["iv_spread"], 0.03)
        self.assertFalse(result["event_risk_flag"])
        self.assertEqual(result["warning_message"], "")

    def test_summarize_option_iv_includes_term_structure_summary(self):
        short_expiration = self._future_date(7)
        longer_expiration = self._future_date(30)
        contracts = [
            {
                "symbol": "AAPL260528C00195000",
                "contract_type": "call",
                "strike_price": 195.0,
                "expiration_date": short_expiration,
                "implied_volatility": 0.94,
            },
            {
                "symbol": "AAPL260528P00195000",
                "contract_type": "put",
                "strike_price": 195.0,
                "expiration_date": short_expiration,
                "implied_volatility": 0.96,
            },
            {
                "symbol": "AAPL260620C00195000",
                "contract_type": "call",
                "strike_price": 195.0,
                "expiration_date": longer_expiration,
                "implied_volatility": 0.54,
            },
            {
                "symbol": "AAPL260620P00195000",
                "contract_type": "put",
                "strike_price": 195.0,
                "expiration_date": longer_expiration,
                "implied_volatility": 0.56,
            },
        ]

        result = summarize_option_iv(contracts, 197.0)

        self.assertIn("term_structure", result)
        self.assertTrue(result["term_structure"]["event_risk_flag"])

    def test_summarize_option_iv_includes_atm_iv_vs_hv_summary(self):
        contracts = [
            {
                "symbol": "AAPL260619C00195000",
                "contract_type": "call",
                "strike_price": 195.0,
                "expiration_date": "2026-06-19",
                "implied_volatility": 0.69,
            },
            {
                "symbol": "AAPL260619P00195000",
                "contract_type": "put",
                "strike_price": 195.0,
                "expiration_date": "2026-06-19",
                "implied_volatility": 0.71,
            },
        ]

        result = summarize_option_iv(
            contracts,
            197.0,
            historical_volatility=0.35,
        )

        self.assertTrue(result["atm_iv_vs_hv"]["available"])
        self.assertEqual(result["atm_iv_vs_hv"]["iv_hv_ratio"], 2.0)
        self.assertEqual(result["atm_iv_vs_hv"]["pricing_band"], "expensive")


if __name__ == "__main__":
    unittest.main()
