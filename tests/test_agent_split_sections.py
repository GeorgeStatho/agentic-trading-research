from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

import agent_pipeline.main as agent_pipeline_main  # noqa: E402
import agent_pipeline.news_refresh as agent_pipeline_news_refresh  # noqa: E402
import agent_pipeline.target_selection as agent_pipeline_target_selection  # noqa: E402
import agent_stages.strategist as strategist_facade  # noqa: E402
import agent_stages.strategist_prompt as strategist_prompt_module  # noqa: E402
import agent_stages.strategist_runner as strategist_runner_module  # noqa: E402
from agent_pipeline.existing_db_view import (  # noqa: E402
    _build_industry_result_from_existing_data,
)
from agent_pipeline.target_selection import (  # noqa: E402
    build_company_opportunist_summary,
    get_current_pipeline_targets,
)
from agent_stages.strategist_normalization import (  # noqa: E402
    extract_recommendation,
    extract_recommendation_from_text,
    normalize_recommendation,
)
from agent_stages.strategist_prompt import (  # noqa: E402
    build_context_snapshot,
    build_no_evidence_result,
    payload_has_evidence,
)
from agent_stages.strategist_runner import decide_company_purchase  # noqa: E402


class PipelineSplitTests(unittest.TestCase):
    def test_pipeline_facade_reexports_split_entrypoints(self):
        self.assertIs(agent_pipeline_main.run_agent_pipeline, agent_pipeline_news_refresh.run_agent_pipeline)
        self.assertIs(
            agent_pipeline_main.get_current_pipeline_targets,
            agent_pipeline_target_selection.get_current_pipeline_targets,
        )

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

    @patch("agent_pipeline.existing_db_view.get_company_opportunist_summary")
    @patch("agent_pipeline.existing_db_view.collect_ranked_companies_for_industry")
    def test_existing_db_industry_builder_threads_ranking_window_into_company_summaries(
        self,
        mock_collect_ranked_companies,
        mock_get_company_summary,
    ):
        mock_collect_ranked_companies.return_value = {
            "industry": {"industry_key": "software", "name": "Software"},
            "selected_companies": [
                {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
                {"company_id": 2, "symbol": "MSFT", "name": "Microsoft"},
            ],
        }
        mock_get_company_summary.side_effect = [
            {"company": {"symbol": "AAPL"}, "impact_count": 2},
            {"company": {"symbol": "MSFT"}, "impact_count": 1},
        ]

        result = _build_industry_result_from_existing_data(
            "software",
            top_company_count=2,
            ranking_max_age_days=7,
        )

        self.assertEqual(result["industry"]["industry_key"], "software")
        self.assertEqual(len(result["company_opportunist_summaries"]), 2)
        mock_get_company_summary.assert_any_call("AAPL", max_age_days=7)
        mock_get_company_summary.assert_any_call("MSFT", max_age_days=7)

    def test_build_company_opportunist_summary_counts_and_dedupes_reasons(self):
        result = build_company_opportunist_summary(
            {
                "company": {
                    "company_id": 1,
                    "symbol": "AAPL",
                    "name": "Apple",
                    "industry_key": "software",
                    "sector_key": "technology",
                },
                "impacts": [
                    {
                        "confidence": "high",
                        "impact_direction": "positive",
                        "impact_magnitude": "major",
                        "reason": "Strong demand tailwind",
                    },
                    {
                        "confidence": "high",
                        "impact_direction": "positive",
                        "impact_magnitude": "major",
                        "reason": "Strong demand tailwind",
                    },
                    {
                        "confidence": "medium",
                        "impact_direction": "negative",
                        "impact_magnitude": "minor",
                        "reason": "Margin pressure risk",
                    },
                ],
            }
        )

        self.assertEqual(result["impact_count"], 3)
        self.assertEqual(result["confidence_counts"], {"high": 2, "medium": 1})
        self.assertEqual(result["direction_counts"], {"positive": 2, "negative": 1})
        self.assertEqual(result["magnitude_counts"], {"major": 2, "minor": 1})
        self.assertEqual(result["sample_reasons"], ["Strong demand tailwind", "Margin pressure risk"])


class StrategistSplitTests(unittest.TestCase):
    def test_strategist_facade_reexports_split_entrypoints(self):
        self.assertIs(strategist_facade.build_strategist_prompt, strategist_prompt_module.build_strategist_prompt)
        self.assertIs(strategist_facade.decide_company_purchase, strategist_runner_module.decide_company_purchase)

    def test_payload_helpers_build_snapshot_and_no_evidence_result(self):
        payload = {
            "company": {
                "company_id": 1,
                "symbol": "AAPL",
                "historical_price_data": {
                    "1d": {"available": True},
                    "5d": {"available": False},
                    "1mo": {"available": True},
                },
            },
            "views": {
                "company": {"count": "2"},
                "industry": {"count": 1},
            },
            "supporting_articles": {
                "article_summaries": [{"title": "Article"}],
                "full_articles": [],
            },
        }

        self.assertTrue(payload_has_evidence(payload))
        snapshot = build_context_snapshot(payload)
        self.assertEqual(snapshot["view_counts"], {"company": 2, "industry": 1})
        self.assertEqual(snapshot["article_summary_count"], 1)
        self.assertEqual(snapshot["full_article_count"], 0)
        self.assertEqual(snapshot["historical_periods_available"], 2)

        no_evidence_result = build_no_evidence_result(payload["company"], context_snapshot=snapshot)
        self.assertEqual(no_evidence_result["recommendation"]["decision"], "do_not_trade")
        self.assertEqual(no_evidence_result["context_snapshot"], snapshot)

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

    @patch("agent_stages.strategist_runner._save_strategist_summary")
    @patch("agent_stages.strategist_runner.extract_json_value")
    @patch("agent_stages.strategist_runner.ask_model")
    @patch("agent_stages.strategist_runner.build_strategist_input")
    def test_decide_company_purchase_falls_back_to_text_parsing_when_json_parse_fails(
        self,
        mock_build_strategist_input,
        mock_ask_model,
        mock_extract_json_value,
        mock_save_summary,
    ):
        mock_build_strategist_input.return_value = {
            "company": {
                "company_id": 1,
                "symbol": "AAPL",
                "name": "Apple",
                "historical_price_data": {},
            },
            "views": {"company": {"count": 2}},
            "supporting_articles": {"article_summaries": [{"title": "Article"}], "full_articles": []},
            "peer_groups": {},
            "filters": {"max_age_days": 5},
            "opportunist_rollup": {},
        }
        mock_ask_model.return_value = """
Recommendation: buy
Confidence: high
Preferred option direction: call
Expected stock direction: up
Summary: Fresh evidence supports a bullish setup.
Thesis:
- Direct company catalysts are strengthening
Risks:
- Sentiment could reverse quickly
"""
        mock_extract_json_value.return_value = None

        result = decide_company_purchase("AAPL", client=object(), model="test-model")

        self.assertEqual(result["recommendation"]["decision"], "trade_candidate")
        self.assertEqual(result["recommendation"]["preferred_option_direction"], "call")
        self.assertEqual(result["recommendation"]["expected_stock_direction"], "up")
        mock_ask_model.assert_called_once()
        mock_save_summary.assert_called_once()


if __name__ == "__main__":
    unittest.main()
