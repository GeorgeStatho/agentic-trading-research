from __future__ import annotations

import json
import unittest

from agent_helpers.shared import build_model_response_diagnostic
from agent_stages.manager_normalization import normalize_recommendation
from agent_stages.manager_prompt import build_manager_prompt
from agent_stages.manager_shared import MANAGER_REASON_MAX_CHARS, MANAGER_RECOMMENDATION_SCHEMA
from services.option_dte_buckets import DEFAULT_MANAGER_TARGET_DTE_BUCKET, TIME_HORIZON_TO_DTE_BUCKET


class ManagerResponseContractTests(unittest.TestCase):
    def test_schema_matches_required_prompt_fields_and_bounds_reason(self) -> None:
        recommendation_schema = MANAGER_RECOMMENDATION_SCHEMA["properties"]["recommendation"]

        self.assertEqual(recommendation_schema["properties"]["reason"]["maxLength"], MANAGER_REASON_MAX_CHARS)
        self.assertTrue(
            {
                "decision",
                "confidence",
                "target_dte_bucket",
                "technical_confirmation",
                "entry_quality",
                "recommended_action_adjustment",
                "reason",
            }.issubset(recommendation_schema["required"])
        )
        self.assertIn("technical_confirmation", recommendation_schema["properties"])
        self.assertIn("overextension_risk", recommendation_schema["properties"])

        system_prompt, user_prompt = build_manager_prompt({"company": {"symbol": "INTC"}})
        self.assertIn(f"at most {MANAGER_REASON_MAX_CHARS} characters", system_prompt)
        self.assertIn("technical_confirmation", json.loads(user_prompt)["required_output"]["recommendation"])

    def test_normalization_caps_an_overlong_reason(self) -> None:
        normalized = normalize_recommendation(
            {
                "decision": "neither",
                "confidence": "high",
                "target_dte_bucket": "none",
                "reason": "x" * (MANAGER_REASON_MAX_CHARS + 25),
            }
        )

        self.assertIsNotNone(normalized)
        self.assertEqual(len(normalized["reason"]), MANAGER_REASON_MAX_CHARS)

    def test_a_trade_with_bucket_none_gets_the_strategist_bucket(self) -> None:
        normalized = normalize_recommendation(
            {"decision": "call", "confidence": "high", "target_dte_bucket": "none", "reason": "Strong setup"},
            strategist_recommendation={"time_horizon": "short_term"},
        )
        self.assertEqual(normalized["target_dte_bucket"], TIME_HORIZON_TO_DTE_BUCKET["short_term"])

    def test_a_trade_with_bucket_none_and_no_horizon_gets_the_default_bucket(self) -> None:
        normalized = normalize_recommendation(
            {"decision": "put", "confidence": "high", "target_dte_bucket": "none", "reason": "Strong setup"})
        self.assertEqual(normalized["target_dte_bucket"], DEFAULT_MANAGER_TARGET_DTE_BUCKET)

    def test_no_trade_keeps_bucket_none(self) -> None:
        normalized = normalize_recommendation(
            {"decision": "neither", "confidence": "low", "target_dte_bucket": "none", "reason": "Weak setup"})
        self.assertEqual(normalized["target_dte_bucket"], "none")

    def test_invalid_json_diagnostic_includes_parse_and_provider_metadata(self) -> None:
        diagnostic = build_model_response_diagnostic(
            '{"recommendation": {"decision": "neither"',
            response_diagnostic={"provider": "vertex", "finish_reason": "MAX_TOKENS"},
            excerpt_characters=20,
        )

        self.assertEqual(diagnostic["provider_response"]["finish_reason"], "MAX_TOKENS")
        self.assertIn("json_decode_error", diagnostic)
        self.assertEqual(diagnostic["json_decode_error"]["line"], 1)
        self.assertTrue(diagnostic["response_excerpt_truncated"])
        self.assertIn("response_tail", diagnostic)


if __name__ == "__main__":
    unittest.main()
