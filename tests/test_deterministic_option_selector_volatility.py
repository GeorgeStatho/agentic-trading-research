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
        implied_volatility: float | None,
        iv_percentile: float | None,
        midpoint_price: float = 2.0,
        delta: float = 0.28,
        open_interest: float = 250.0,
        vega: float = 0.11,
        contract_type: str = "call",
    ) -> dict[str, object]:
        contract = {
            "option_id": option_id,
            "symbol": symbol,
            "contract_type": contract_type,
            "expiration_date": self._future_date(expiration_days),
            "strike_price": strike_price,
            "open_interest": open_interest,
            "latest_quote": {
                "bid_price": round(midpoint_price - 0.05, 2),
                "ask_price": round(midpoint_price + 0.05, 2),
                "midpoint_price": midpoint_price,
            },
            "greeks": {
                "delta": delta,
                "gamma": 0.02,
                "theta": -0.08,
                "vega": vega,
            },
        }
        if implied_volatility is not None:
            contract["implied_volatility"] = implied_volatility
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
    def test_selector_downgrades_confidence_but_does_not_reject_when_non_vega_volatility_is_too_expensive(self):
        short_contract = self._build_contract(
            option_id=303,
            symbol="AAPL_FRONT",
            expiration_days=7,
            strike_price=101.0,
            implied_volatility=0.95,
            iv_percentile=88.0,
            vega=0.04,
        )
        manager_result = self._build_manager_result(
            confidence="medium",
            contracts=[short_contract],
            target_dte_bucket="1_7",
            term_structure_event=True,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertIsNotNone(result["selected_option"])
        self.assertEqual(result["recommendation"]["selected_option_id"], 303)
        self.assertEqual(result["recommendation"]["confidence"], "medium")
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "low")
        self.assertEqual(
            result["recommendation"]["selected_option_source"],
            "deterministic_low_confidence_simple_preferred_near_otm",
        )
        self.assertEqual(
            result["recommendation"]["selection_debug"]["volatility_guardrail_reason"],
            "",
        )
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_confidence_penalty_points"],
            0,
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
    def test_selector_prefers_moderate_vega_per_premium_when_iv_is_low(self):
        preferred_contract = self._build_contract(
            option_id=451,
            symbol="AAPL_LOW_IV_PREFERRED_VEGA",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.28,
            iv_percentile=20.0,
            vega=0.06,
        )
        lower_vega_contract = self._build_contract(
            option_id=452,
            symbol="AAPL_LOW_IV_LOW_VEGA",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.28,
            iv_percentile=20.0,
            vega=0.01,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[lower_vega_contract, preferred_contract],
            term_structure_event=False,
            hv_20d=0.30,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 451)
        self.assertEqual(result["selected_option"]["symbol"], "AAPL_LOW_IV_PREFERRED_VEGA")
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_profile"]["band"],
            "preferred_low_iv_vega",
        )
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_selection_preference_score"],
            -1.0,
        )

    @patch.object(selector, "OPTION_SELECTOR_MODE", "hybrid")
    def test_hybrid_selector_prefers_moderate_vega_per_premium_when_iv_is_low(self):
        preferred_contract = self._build_contract(
            option_id=453,
            symbol="AAPL_LOW_IV_PREFERRED_VEGA_HYBRID",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.28,
            iv_percentile=20.0,
            vega=0.06,
        )
        lower_vega_contract = self._build_contract(
            option_id=454,
            symbol="AAPL_LOW_IV_LOW_VEGA_HYBRID",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.28,
            iv_percentile=20.0,
            vega=0.01,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[lower_vega_contract, preferred_contract],
            term_structure_event=False,
            hv_20d=0.30,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 453)
        self.assertEqual(result["selected_option"]["symbol"], "AAPL_LOW_IV_PREFERRED_VEGA_HYBRID")
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_profile"]["band"],
            "preferred_low_iv_vega",
        )

    @patch.object(selector, "OPTION_SELECTOR_MODE", "greeks")
    def test_greeks_selector_prefers_moderate_vega_per_premium_when_iv_is_low(self):
        preferred_contract = self._build_contract(
            option_id=455,
            symbol="AAPL_LOW_IV_PREFERRED_VEGA_GREEKS",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.28,
            iv_percentile=20.0,
            vega=0.06,
            delta=0.30,
        )
        lower_vega_contract = self._build_contract(
            option_id=456,
            symbol="AAPL_LOW_IV_LOW_VEGA_GREEKS",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.28,
            iv_percentile=20.0,
            vega=0.01,
            delta=0.30,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[lower_vega_contract, preferred_contract],
            term_structure_event=False,
            hv_20d=0.30,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 455)
        self.assertEqual(result["selected_option"]["symbol"], "AAPL_LOW_IV_PREFERRED_VEGA_GREEKS")
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_profile"]["band"],
            "preferred_low_iv_vega",
        )

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_selector_penalizes_very_high_vega_per_premium_when_iv_is_high(self):
        high_vega_contract = self._build_contract(
            option_id=461,
            symbol="AAPL_HIGH_IV_HIGH_VEGA",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.70,
            iv_percentile=75.0,
            vega=0.12,
        )
        lower_vega_contract = self._build_contract(
            option_id=462,
            symbol="AAPL_HIGH_IV_LOWER_VEGA",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.70,
            iv_percentile=75.0,
            vega=0.04,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[high_vega_contract, lower_vega_contract],
            term_structure_event=False,
            hv_20d=0.70,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 462)
        self.assertEqual(result["selected_option"]["symbol"], "AAPL_HIGH_IV_LOWER_VEGA")
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_profile"]["band"],
            "acceptable_elevated_iv_vega",
        )
        self.assertEqual(result["recommendation"]["confidence"], "high")
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "medium")

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_selector_can_reject_medium_confidence_trade_when_high_iv_vega_is_unusually_high(self):
        contract = self._build_contract(
            option_id=463,
            symbol="AAPL_HIGH_IV_VERY_HIGH_VEGA",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.70,
            iv_percentile=75.0,
            vega=0.12,
        )
        manager_result = self._build_manager_result(
            confidence="medium",
            contracts=[contract],
            term_structure_event=False,
            hv_20d=0.70,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertIsNone(result["selected_option"])
        self.assertEqual(result["recommendation"]["confidence"], "medium")
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "low")
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_profile"]["band"],
            "very_high_vega_in_high_iv",
        )

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_vega_does_not_override_better_otm_fit_during_ranking(self):
        better_otm_contract = self._build_contract(
            option_id=470,
            symbol="AAPL_BETTER_OTM_HIGH_VEGA",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=0.72,
            iv_percentile=75.0,
            vega=0.12,
            midpoint_price=2.0,
        )
        worse_otm_contract = self._build_contract(
            option_id=471,
            symbol="AAPL_WORSE_OTM_LOW_VEGA",
            expiration_days=30,
            strike_price=103.0,
            implied_volatility=0.72,
            iv_percentile=75.0,
            vega=0.04,
            midpoint_price=2.0,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[worse_otm_contract, better_otm_contract],
            term_structure_event=False,
            hv_20d=0.72,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 470)
        self.assertEqual(result["selected_option"]["symbol"], "AAPL_BETTER_OTM_HIGH_VEGA")
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_profile"]["band"],
            "very_high_vega_in_high_iv",
        )
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["core_penalty_points"],
            1,
        )
        self.assertEqual(
            result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]["vega_confidence_penalty_points"],
            1,
        )
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "medium")

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

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_simple_selector_prefers_better_profit_path_when_contract_shape_is_similar(self):
        better_profit_path = self._build_contract(
            option_id=601,
            symbol="AAPL_BETTER_PROFIT_PATH_SIMPLE",
            expiration_days=30,
            strike_price=103.0,
            implied_volatility=0.35,
            iv_percentile=45.0,
            midpoint_price=2.0,
            vega=0.04,
        )
        worse_profit_path = self._build_contract(
            option_id=602,
            symbol="AAPL_WORSE_PROFIT_PATH_SIMPLE",
            expiration_days=30,
            strike_price=103.0,
            implied_volatility=0.35,
            iv_percentile=45.0,
            midpoint_price=8.0,
            vega=0.04,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[worse_profit_path, better_profit_path],
            term_structure_event=False,
            hv_20d=0.35,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 601)
        assessment = result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]
        self.assertEqual(assessment["profit_path"]["band"], "strong_path_to_profit")
        self.assertEqual(assessment["profit_path_selection_preference_score"], -1.0)

    @patch.object(selector, "OPTION_SELECTOR_MODE", "hybrid")
    def test_hybrid_selector_prefers_better_profit_path_when_contract_shape_is_similar(self):
        better_profit_path = self._build_contract(
            option_id=603,
            symbol="AAPL_BETTER_PROFIT_PATH_HYBRID",
            expiration_days=30,
            strike_price=103.0,
            implied_volatility=0.35,
            iv_percentile=45.0,
            midpoint_price=2.0,
            vega=0.04,
            delta=0.38,
        )
        worse_profit_path = self._build_contract(
            option_id=604,
            symbol="AAPL_WORSE_PROFIT_PATH_HYBRID",
            expiration_days=30,
            strike_price=103.0,
            implied_volatility=0.35,
            iv_percentile=45.0,
            midpoint_price=8.0,
            vega=0.04,
            delta=0.38,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[worse_profit_path, better_profit_path],
            term_structure_event=False,
            hv_20d=0.35,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 603)
        assessment = result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]
        self.assertEqual(assessment["profit_path"]["band"], "strong_path_to_profit")

    @patch.object(selector, "OPTION_SELECTOR_MODE", "greeks")
    def test_greeks_selector_prefers_better_profit_path_when_contract_shape_is_similar(self):
        better_profit_path = self._build_contract(
            option_id=605,
            symbol="AAPL_BETTER_PROFIT_PATH_GREEKS",
            expiration_days=30,
            strike_price=103.0,
            implied_volatility=0.35,
            iv_percentile=45.0,
            midpoint_price=2.0,
            vega=0.04,
            delta=0.42,
        )
        worse_profit_path = self._build_contract(
            option_id=606,
            symbol="AAPL_WORSE_PROFIT_PATH_GREEKS",
            expiration_days=30,
            strike_price=103.0,
            implied_volatility=0.35,
            iv_percentile=45.0,
            midpoint_price=8.0,
            vega=0.04,
            delta=0.42,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[worse_profit_path, better_profit_path],
            term_structure_event=False,
            hv_20d=0.35,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertEqual(result["recommendation"]["selected_option_id"], 605)
        assessment = result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]
        self.assertEqual(assessment["profit_path"]["band"], "strong_path_to_profit")

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_profit_path_assessment_is_neutral_when_contract_iv_is_missing(self):
        contract = self._build_contract(
            option_id=607,
            symbol="AAPL_MISSING_IV_PROFIT_PATH",
            expiration_days=30,
            strike_price=101.0,
            implied_volatility=None,
            iv_percentile=45.0,
            midpoint_price=2.0,
            vega=0.04,
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[contract],
            term_structure_event=False,
            hv_20d=0.35,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        self.assertIsNotNone(result["selected_option"])
        self.assertEqual(result["recommendation"]["confidence"], "high")
        self.assertEqual(result["recommendation"]["confidence_after_volatility"], "high")
        assessment = result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]
        self.assertFalse(assessment["profit_path"]["available"])
        self.assertEqual(assessment["profit_path"]["band"], "unknown")
        self.assertEqual(assessment["profit_path_selection_preference_score"], 0.0)

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_profit_path_uses_straddle_expected_move_when_near_term_event_pricing_is_present(self):
        call_contract = self._build_contract(
            option_id=608,
            symbol="AAPL_EVENT_CALL",
            expiration_days=7,
            strike_price=101.0,
            implied_volatility=0.20,
            iv_percentile=75.0,
            midpoint_price=5.0,
            vega=0.04,
            contract_type="call",
        )
        companion_put = self._build_contract(
            option_id=609,
            symbol="AAPL_EVENT_PUT",
            expiration_days=7,
            strike_price=101.0,
            implied_volatility=0.20,
            iv_percentile=75.0,
            midpoint_price=5.0,
            vega=0.04,
            contract_type="put",
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[call_contract, companion_put],
            target_dte_bucket="1_7",
            term_structure_event=True,
            hv_20d=0.20,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        assessment = result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]
        self.assertEqual(result["recommendation"]["selected_option_id"], 608)
        self.assertEqual(assessment["profit_path"]["expected_move_source"], "straddle_event")
        self.assertEqual(
            assessment["profit_path"]["straddle_implied_expected_move"]["expected_move_dollars"],
            10.0,
        )
        self.assertEqual(assessment["profit_path"]["expected_move_dollars"], 10.0)
        self.assertEqual(assessment["profit_path"]["band"], "strong_path_to_profit")

    @patch.object(selector, "OPTION_SELECTOR_MODE", "simple")
    def test_profit_path_falls_back_to_iv_model_when_event_pricing_is_not_present(self):
        call_contract = self._build_contract(
            option_id=610,
            symbol="AAPL_NON_EVENT_CALL",
            expiration_days=7,
            strike_price=101.0,
            implied_volatility=0.20,
            iv_percentile=45.0,
            midpoint_price=5.0,
            vega=0.04,
            contract_type="call",
        )
        companion_put = self._build_contract(
            option_id=611,
            symbol="AAPL_NON_EVENT_PUT",
            expiration_days=7,
            strike_price=101.0,
            implied_volatility=0.20,
            iv_percentile=45.0,
            midpoint_price=5.0,
            vega=0.04,
            contract_type="put",
        )
        manager_result = self._build_manager_result(
            confidence="high",
            contracts=[call_contract, companion_put],
            target_dte_bucket="1_7",
            term_structure_event=False,
            hv_20d=0.20,
        )

        result = selector.apply_deterministic_option_selection(manager_result)

        assessment = result["recommendation"]["selection_debug"]["selected_option_volatility_assessment"]
        self.assertEqual(result["recommendation"]["selected_option_id"], 610)
        self.assertEqual(assessment["profit_path"]["expected_move_source"], "contract_iv_dte")
        self.assertEqual(
            assessment["profit_path"]["straddle_implied_expected_move"]["expected_move_dollars"],
            10.0,
        )
        self.assertLess(assessment["profit_path"]["expected_move_dollars"], 3.0)


if __name__ == "__main__":
    unittest.main()
