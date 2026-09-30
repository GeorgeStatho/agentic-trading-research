from __future__ import annotations

import unittest

from agent_helpers.deterministic_option_selector import apply_deterministic_option_selection  # noqa: E402
from services.evidence_continuation_gate import EvidenceContinuationPolicy, evaluate_evidence_continuation_gate  # noqa: E402
from services.order_candidates import OrderCandidateBuilder  # noqa: E402


def _payload(*, relative_volume: float = 1.5, sector_state: str = "outperforming", overextension: str = "low") -> dict:
    return {
        "grounding": {"article_ids": [1, 2]},
        "supporting_articles": {
            "article_summaries": [
                {"article_id": 1, "source": "Reuters", "root_event_id": "root:reuters", "root_source_id": "wire:reuters", "evidence_score": 0.82},
                {"article_id": 2, "source": "Bloomberg", "root_event_id": "root:bloomberg", "root_source_id": "publisher:bloomberg", "evidence_score": 0.74},
            ]
        },
        "market_context": {
            "underlying_price_history": {
                "technical_summary": {
                    "trend_state": "bullish",
                    "relative_strength_confirmation": "strong",
                    "volume_confirmation": "strong",
                    "overextension_risk": overextension,
                    "call_entry_quality": "excellent",
                    "put_entry_quality": "poor",
                },
                "technical_indicators": {
                    "relative_volume": {"relative_volume_20d": relative_volume},
                    "sector_market_relative_strength": {
                        "available": True,
                        "sector_etf": "XLK",
                        "benchmark": "SPY",
                        "relative_return_20d_pct": 1.2,
                        "state": sector_state,
                    },
                },
            },
            "option_market": {"contracts": []},
        },
    }


class EvidenceContinuationGateTests(unittest.TestCase):
    def _gate(self, payload: dict) -> dict:
        return evaluate_evidence_continuation_gate(
            recommendation={"decision": "call", "confidence": "high"},
            payload=payload,
            market_context=payload["market_context"],
            policy=EvidenceContinuationPolicy(enforcement_mode="enforce"),
        )

    def test_allows_grounded_bullish_daily_continuation(self) -> None:
        result = self._gate(_payload())

        self.assertTrue(result["allowed"])
        self.assertEqual(result["eligible_decision"], "call")
        self.assertEqual(result["evidence"]["citation_count"], 2)
        self.assertEqual(result["evidence"]["independent_source_count"], 2)

    def test_rejects_weak_relative_volume(self) -> None:
        result = self._gate(_payload(relative_volume=1.1))

        self.assertFalse(result["allowed"])
        self.assertIn("relative_volume_below_minimum", result["reason_codes"])

    def test_rejects_sector_underperformance(self) -> None:
        result = self._gate(_payload(sector_state="underperforming"))

        self.assertFalse(result["allowed"])
        self.assertIn("sector_not_outperforming_market", result["reason_codes"])

    def test_fails_closed_when_technical_data_is_missing(self) -> None:
        payload = _payload()
        payload["market_context"]["underlying_price_history"] = {}
        result = self._gate(payload)

        self.assertFalse(result["allowed"])
        self.assertIn("missing_technical_data", result["reason_codes"])

    def test_rejects_overextended_price_as_chase_risk(self) -> None:
        result = self._gate(_payload(overextension="high"))

        self.assertFalse(result["allowed"])
        self.assertIn("chase_risk", result["reason_codes"])

    def test_shadow_mode_records_block_without_preventing_an_entry(self) -> None:
        payload = _payload(relative_volume=1.0)
        result = evaluate_evidence_continuation_gate(
            recommendation={"decision": "call", "confidence": "high"},
            payload=payload,
            market_context=payload["market_context"],
            policy=EvidenceContinuationPolicy(enforcement_mode="shadow"),
        )

        self.assertTrue(result["allowed"])
        self.assertFalse(result["would_allow"])
        self.assertEqual(result["enforcement_mode"], "shadow")

    def test_allows_symmetric_bearish_put_continuation(self) -> None:
        payload = _payload(sector_state="underperforming")
        payload["market_context"]["underlying_price_history"]["technical_summary"].update(
            {
                "trend_state": "bearish",
                "relative_strength_confirmation": "weak",
                "put_entry_quality": "good",
            }
        )
        result = evaluate_evidence_continuation_gate(
            recommendation={"decision": "put", "confidence": "high"},
            payload=payload,
            market_context=payload["market_context"],
            policy=EvidenceContinuationPolicy(enforcement_mode="enforce"),
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["eligible_decision"], "put")

    def test_selector_does_not_pick_a_contract_when_gate_is_blocked(self) -> None:
        payload = _payload()
        blocked_gate = self._gate(_payload(relative_volume=1.0))
        result = apply_deterministic_option_selection(
            {
                "recommendation": {"decision": "call", "confidence": "high"},
                "market_context": payload["market_context"],
                "entry_gate": blocked_gate,
                "strategist_recommendation": {"decision": "trade_candidate"},
            }
        )

        self.assertIsNone(result["selected_option"])
        self.assertEqual(result["recommendation"]["selected_option_source"], "evidence_continuation_blocked")

    def test_selector_records_no_contract_after_continuation_is_confirmed(self) -> None:
        payload = _payload()
        allowed_gate = self._gate(payload)
        result = apply_deterministic_option_selection(
            {
                "recommendation": {"decision": "call", "confidence": "high"},
                "market_context": payload["market_context"],
                "entry_gate": allowed_gate,
                "strategist_recommendation": {"decision": "trade_candidate"},
            }
        )

        self.assertIsNone(result["selected_option"])
        self.assertEqual(
            result["recommendation"]["selected_option_source"],
            "continuation_confirmed_but_no_eligible_contract",
        )

    def test_batch_and_streaming_candidate_shapes_require_allowed_gate(self) -> None:
        builder = OrderCandidateBuilder()
        base_company = {
            "company_id": 1,
            "symbol": "MSFT",
            "decision": "call",
            "confidence": "high",
            "strategist_recommendation": {"decision": "trade_candidate"},
            "selected_option": {"symbol": "MSFT260619C00450000"},
        }
        self.assertEqual(
            builder.build_from_company_results(
                [{**base_company, "entry_gate": {"allowed": False}, "market_regime_gate": {"allowed": True}}]
            ),
            [],
        )
        self.assertIsNone(
            builder.build_from_manager_result(
                {
                    "company": {"company_id": 1, "symbol": "MSFT"},
                    "recommendation": {"decision": "call", "confidence": "high"},
                    "strategist_recommendation": {"decision": "trade_candidate"},
                    "selected_option": {"symbol": "MSFT260619C00450000"},
                    "entry_gate": {"allowed": False},
                    "market_regime_gate": {"allowed": True},
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
