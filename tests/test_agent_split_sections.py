from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_pipeline.target_selection import get_current_pipeline_targets
from agent_stages.strategist_normalization import (  # noqa: E402
    extract_recommendation,
    extract_recommendation_from_text,
    normalize_recommendation,
)
from agent_stages.strategist_runner import decide_company_purchase  # noqa: E402


class VerboseTestCase(unittest.TestCase):
    def log_pass(self, message: str) -> None:
        print(f"[PASS] {self.__class__.__name__}.{self._testMethodName}: {message}")


class PipelineSplitTests(VerboseTestCase):
    @patch("agent_pipeline.target_selection.collect_ranked_companies_for_industry")
    @patch("agent_pipeline.target_selection.get_current_rankings")
    def test_get_current_pipeline_targets_dedupes_companies_across_industries(
        self,
        mock_get_current_rankings,
        mock_collect_ranked_companies,
    ):
        mock_get_current_rankings.return_value = {
            "top_sectors": [{"sector_key": "technology"}],
            "top_industries_by_sector": {
                "technology": [
                    {"industry_key": "software"},
                    {"industry_key": "semiconductors"},
                ]
            },
            "ranking_max_age_days": 7,
        }
        mock_collect_ranked_companies.side_effect = [
            {
                "industry": {"industry_key": "software"},
                "selected_companies": [
                    {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
                    {"company_id": 2, "symbol": "MSFT", "name": "Microsoft"},
                ],
            },
            {
                "industry": {"industry_key": "semiconductors"},
                "selected_companies": [
                    {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
                    {"company_id": 3, "symbol": "NVDA", "name": "NVIDIA"},
                ],
            },
        ]

        result = get_current_pipeline_targets(
            top_sector_count=1,
            top_industry_count=2,
            top_company_count=2,
            ranking_max_age_days=7,
        )

        self.assertEqual(result["top_sector_keys"], ["technology"])
        self.assertEqual(result["top_industry_keys"], ["software", "semiconductors"])
        self.assertEqual(result["selected_company_ids"], [1, 2, 3])
        self.assertEqual(result["selected_company_symbols"], ["AAPL", "MSFT", "NVDA"])
        self.assertEqual(result["ranking_max_age_days"], 7)
        self.log_pass("pipeline targets deduped overlapping companies across ranked industries")


class StrategistSplitTests(VerboseTestCase):
    def test_extract_and_normalize_recommendation_handles_aliases_and_defaults(self):
        nested_payload = {
            "analysis": {
                "recommendation": {
                    "decision": "buy",
                    "confidence": "moderate",
                    "option_bias": "bullish",
                    "bull_case": ["Services growth supports upside"],
                    "risk_factors": "Regulatory pressure",
                }
            }
        }

        recommendation = extract_recommendation(nested_payload)
        normalized = normalize_recommendation(recommendation)

        self.assertEqual(normalized["decision"], "trade_candidate")
        self.assertEqual(normalized["confidence"], "medium")
        self.assertEqual(normalized["preferred_option_direction"], "call")
        self.assertEqual(normalized["expected_stock_direction"], "up")
        self.assertEqual(normalized["evidence_quality"], "moderate")
        self.assertEqual(normalized["setup_quality"], "moderate")
        self.assertEqual(normalized["summary"], "Services growth supports upside")
        self.assertEqual(normalized["why_now"], "Services growth supports upside")
        self.assertEqual(normalized["thesis"], ["Services growth supports upside"])
        self.assertEqual(normalized["risks"], ["Regulatory pressure"])
        self.assertFalse(normalized["contradictions_present"])
        self.log_pass("strategist normalization converted aliases and defaults into the canonical recommendation shape")

    def test_extract_recommendation_from_text_parses_labeled_response(self):
        raw_response = """
Recommendation: buy
Confidence: high
Preferred option direction: call
Expected stock direction: up
Summary: Demand and margins are both improving.
Thesis:
- Product cycle remains strong
- Services mix is expanding
Risks:
- Valuation could compress
"""

        recommendation = extract_recommendation_from_text(raw_response)

        self.assertEqual(recommendation["decision"], "trade_candidate")
        self.assertEqual(recommendation["confidence"], "high")
        self.assertEqual(recommendation["preferred_option_direction"], "call")
        self.assertEqual(recommendation["expected_stock_direction"], "up")
        self.assertEqual(recommendation["thesis"], ["Product cycle remains strong", "Services mix is expanding"])
        self.assertEqual(recommendation["risks"], ["Valuation could compress"])
        self.log_pass("text fallback parsing recovered a structured strategist recommendation from labeled prose")

    @patch("agent_stages.strategist_runner._save_strategist_summary")
    @patch("agent_stages.strategist_runner.ask_model")
    @patch("agent_stages.strategist_runner.build_strategist_input")
    def test_decide_company_purchase_short_circuits_when_no_evidence(
        self,
        mock_build_strategist_input,
        mock_ask_model,
        mock_save_summary,
    ):
        mock_build_strategist_input.return_value = {
            "company": {
                "company_id": 1,
                "symbol": "AAPL",
                "name": "Apple",
                "historical_price_data": {},
            },
            "views": {"company": {"count": 0}},
            "supporting_articles": {"article_summaries": [], "full_articles": []},
        }

        result = decide_company_purchase("AAPL", client=object(), model="test-model")

        self.assertEqual(result["recommendation"]["decision"], "do_not_trade")
        mock_ask_model.assert_not_called()
        mock_save_summary.assert_called_once()
        self.log_pass("strategist runner short-circuited to do-not-trade when no evidence was present")


if __name__ == "__main__":
    unittest.main()
