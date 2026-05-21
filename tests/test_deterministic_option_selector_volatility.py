from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

import agent_helpers.deterministic_option_selector as selector  # noqa: E402


class DeterministicSelectorVolatilityTests(unittest.TestCase):
    @staticmethod
    def _future_date(days_from_today: int) -> str:
        return (date.today() + timedelta(days=days_from_today)).isoformat()

    def _build_contract(
        self,
        *,
        option_id: int,
        symbol: str,
        expiration_days: int,
        strike_price: float,
        implied_volatility: float,
        iv_percentile: float | None,
        midpoint_price: float = 2.0,
        delta: float = 0.28,
        open_interest: float = 250.0,
    ) -> dict[str, object]:
        contract = {
            "option_id": option_id,
            "symbol": symbol,
            "contract_type": "call",
            "expiration_date": self._future_date(expiration_days),
            "strike_price": strike_price,
            "open_interest": open_interest,
            "implied_volatility": implied_volatility,
            "latest_quote": {
                "bid_price": round(midpoint_price - 0.05, 2),
                "ask_price": round(midpoint_price + 0.05, 2),
                "midpoint_price": midpoint_price,
            },
            "greeks": {
                "delta": delta,
                "gamma": 0.02,
                "theta": -0.08,
                "vega": 0.11,
            },
        }
        if iv_percentile is not None:
            contract["iv_percentile"] = iv_percentile
        return contract

    def _build_manager_result(
        self,
        *,
        confidence: str,
        contracts: list[dict[str, object]],
        target_dte_bucket: str = "none",
        hv_20d: float = 0.35,
        term_structure_event: bool = False,
    ) -> dict[str, object]:
        return {
            "recommendation": {
                "decision": "call",
                "confidence": confidence,
                "target_dte_bucket": target_dte_bucket,
                "reason": "test setup",
            },
            "strategist_recommendation": {
                "decision": "trade_candidate",
            },
            "market_context": {
                "current_stock_price": {
                    "price": 100.0,
                },
                "underlying_price_history": {
                    "available": True,
                    "source": "alpaca_stock_bars",
                    "historical_volatility_20d": hv_20d,
                    "historical_volatility_60d": 0.32,
                },
                "option_market": {
                    "contracts": contracts,
                    "selection_filters": {},
                    "volatility_summary": {
                        "term_structure": {
                            "event_risk_flag": term_structure_event,
                            "short_term_atm_iv": 0.95 if term_structure_event else 0.55,
                            "longer_term_atm_iv": 0.55,
                        },
                    },
                },
            },
        }

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_selector_prefers_longer_dte_when_front_iv_is_event_priced(self):
        short_contract = self._build_contract(
            option_id=101,
            symbol="AAPL_SHORT",
            expiration_days=7,
            strike_price=101.0,
            implied_volatility=0.95,
            iv_percentile=85.0,
        )
        longer_contract = self._build_contract(
            option_id=202,
            symbol="AAPL_LONGER",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.45,
            iv_percentile=45.0,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[short_contract, longer_contract],
            term_structure_event=True,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 202)
        self.assertEqual(result["selected_option"]["symbol"], "AAPL_LONGER")
        self.assertEqual(result["recommendation"]["confidence"], "high")
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "high")
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["term_structure"]["band"],
            "prefer_longer_dte",
        )

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_selector_downgrades_confidence_and_rejects_when_volatility_is_too_expensive(self):
        short_contract = self._build_contract(
            option_id=303,
            symbol="AAPL_FRONT",
            expiration_days=7,
            strike_price=101.0,
            implied_volatility=0.95,
            iv_percentile=88.0,
        )
        manager_result = self._build_manager_result(
            confidence="medium",
            contracts=[short_contract],
            target_dte_bucket="1_7",
            term_structure_event=True,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertIsNone(result["selected_option"])
        self.assertIsNone(result["recommendation"]["selected_option_id"])
        self.assertEqual(result["recommendation"]["confidence"], "medium")
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "low")
        self.assertEqual(
            result["recommendation"]["selected_option_source"],
            "volatility_confidence_below_medium",
        )
        self.assertEqual(
            result["recommendation"]["selection_debug"]["volatility_guardrail_reason"],
            "volatility_confidence_below_medium",
        )

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_selector_reduces_high_confidence_to_medium_for_expensive_but_still_tradeable_volatility(self):
        contract = self._build_contract(
            option_id=404,
            symbol="AAPL_EXPENSIVE",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.58,
            iv_percentile=65.0,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[contract],
            term_structure_event=False,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertIsNotNone(result["selected_option"])
        self.assertEqual(result["recommendation"]["selected_option_id"], 404)
        self.assertEqual(result["recommendation"]["confidence"], "high")
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "medium")
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["iv_hv"]["pricing_band"],
            "expensive",
        )

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_selector_does_not_fallback_to_chain_level_atm_iv_percentile_for_every_contract(self):
        contract = self._build_contract(
            option_id=505,
            symbol="AAPL_BUCKETLESS",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.30,
            iv_percentile=None,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[contract],
            term_structure_event=False,
            hv_20d=0.35,
        )
        manager_result["market_context"]["option_market"]["volatility_summary"].update(
            {
                "atm_iv_percentile": 95.0,
                "dte_bucket_iv_percentiles": {},
            }
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertIsNotNone(result["selected_option"])
        self.assertEqual(result["recommendation"]["confidence"], "high")
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "high")
        self.assertFalse(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["iv_percentile"]["available"]
        )


if __name__ == "__main__":
    unittest.main()
