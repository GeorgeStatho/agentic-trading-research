from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

import main as agent_runner_main  # noqa: E402


class AgentRunnerCompanyFilterTests(unittest.TestCase):
    @patch.object(agent_runner_main, "record_manager_decision_history")
    @patch.object(agent_runner_main, "apply_deterministic_option_selection")
    @patch.object(agent_runner_main, "decide_company_option_position")
    @patch.object(agent_runner_main, "decide_company_purchase")
    def test_run_strategist_and_manager_records_manager_decision_history(
        self,
        mock_decide_company_purchase,
        mock_decide_company_option_position,
        mock_apply_deterministic_option_selection,
        mock_record_manager_decision_history,
    ) -> None:
        mock_decide_company_purchase.return_value = {
            "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
            "recommendation": {"decision": "trade_candidate", "confidence": "high"},
        }
        mock_decide_company_option_position.return_value = {
            "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
            "context_snapshot": {"article_summary_count": 2},
            "market_context": {"option_market": {"contract_count": 1}},
            "supporting_articles": {
                "article_summaries": [
                    {
                        "article_id": 101,
                        "title": "Apple catalyst",
                        "source": "Newswire",
                        "published_at": "2026-05-22T10:00:00+00:00",
                        "article_scope": "company",
                        "evidence_layers": ["company_view"],
                    }
                ],
                "full_articles": [],
            },
            "recent_manager_decision_history": [
                {
                    "decision_run_at": "2026-05-20T10:00:00+00:00",
                    "manager_decision": "call",
                    "manager_confidence": "medium",
                }
            ],
            "strategist_recommendation": {"decision": "trade_candidate", "confidence": "high"},
            "recommendation": {
                "decision": "call",
                "confidence": "high",
                "reason": "Strong setup",
                "target_dte_bucket": "20_30",
                "selected_option_id": "AAPL260619C00190000",
                "selected_expiration_date": "2026-06-19",
                "selected_strike_price": 190.0,
                "selected_option_source": "deterministic_high_confidence_hybrid_filtered_short_swing",
            },
            "selected_option": {"symbol": "AAPL260619C00190000"},
        }
        mock_apply_deterministic_option_selection.side_effect = lambda result: result
        mock_record_manager_decision_history.return_value = 77

        strategist_results, manager_results = agent_runner_main._run_strategist_and_manager(["AAPL"])

        self.assertEqual(len(strategist_results), 1)
        self.assertEqual(len(manager_results), 1)
        self.assertEqual(manager_results[0]["manager_decision_history_id"], 77)
        mock_record_manager_decision_history.assert_called_once()
        recorded_payload = mock_record_manager_decision_history.call_args.args[0]
        self.assertEqual(recorded_payload["company_id"], 1)
        self.assertEqual(recorded_payload["symbol"], "AAPL")
        self.assertEqual(recorded_payload["manager_decision"], "call")
        self.assertEqual(recorded_payload["manager_confidence"], "high")
        self.assertEqual(recorded_payload["selected_option_symbol"], "AAPL260619C00190000")
        self.assertEqual(recorded_payload["manager_input_json"]["article_references"][0]["article_id"], 101)
        self.assertEqual(len(recorded_payload["manager_input_json"]["recent_manager_decision_history"]), 1)

    @patch.object(agent_runner_main, "get_company_opportunist_summary")
    def test_filter_company_symbols_by_high_confidence_support_skips_under_supported_names(
        self,
        mock_get_company_opportunist_summary,
    ) -> None:
        mock_get_company_opportunist_summary.side_effect = [
            {
                "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
                "confidence_counts": {"high": 3, "medium": 1},
            },
            {
                "company": {"company_id": 2, "symbol": "MSFT", "name": "Microsoft"},
                "confidence_counts": {"high": 1},
            },
            {
                "company": {"company_id": 3, "symbol": "NVDA", "name": "NVIDIA"},
                "confidence_counts": {},
            },
        ]

        allowed_symbols, skipped_companies = agent_runner_main._filter_company_symbols_by_high_confidence_support(
            ["aapl", "msft", "nvda", "AAPL"],
            minimum_high_confidence_articles=2,
            max_age_days=5,
        )

        self.assertEqual(allowed_symbols, ["AAPL"])
        self.assertEqual(
            skipped_companies,
            [
                {
                    "symbol": "MSFT",
                    "company_id": 2,
                    "name": "Microsoft",
                    "high_confidence_article_count": 1,
                    "minimum_required_high_confidence_articles": 2,
                    "window_max_age_days": 5,
                    "skip_reason": "insufficient_high_confidence_articles",
                },
                {
                    "symbol": "NVDA",
                    "company_id": 3,
                    "name": "NVIDIA",
                    "high_confidence_article_count": 0,
                    "minimum_required_high_confidence_articles": 2,
                    "window_max_age_days": 5,
                    "skip_reason": "insufficient_high_confidence_articles",
                },
            ],
        )
        mock_get_company_opportunist_summary.assert_any_call("AAPL", max_age_days=5)
        mock_get_company_opportunist_summary.assert_any_call("MSFT", max_age_days=5)
        mock_get_company_opportunist_summary.assert_any_call("NVDA", max_age_days=5)

    @patch.object(agent_runner_main, "apply_deterministic_option_selection")
    @patch.object(agent_runner_main, "decide_company_option_position")
    @patch.object(agent_runner_main, "decide_company_purchase")
    @patch.object(agent_runner_main, "get_company_opportunist_summary")
    @patch.object(agent_runner_main, "run_agent_pipeline_from_existing_data")
    @patch.object(agent_runner_main.AgentPipelineSettings, "from_env")
    def test_run_full_agent_stack_from_existing_data_only_runs_supported_companies(
        self,
        mock_pipeline_settings,
        mock_run_agent_pipeline_from_existing_data,
        mock_get_company_opportunist_summary,
        mock_decide_company_purchase,
        mock_decide_company_option_position,
        mock_apply_deterministic_option_selection,
    ) -> None:
        mock_pipeline_settings.return_value = SimpleNamespace(minimum_high_confidence_company_articles=2)
        mock_run_agent_pipeline_from_existing_data.return_value = {
            "sectors": [
                {
                    "industries": [
                        {
                            "selected_companies": [
                                {"symbol": "AAPL"},
                                {"symbol": "MSFT"},
                            ]
                        }
                    ]
                }
            ]
        }
        mock_get_company_opportunist_summary.side_effect = [
            {
                "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
                "confidence_counts": {"high": 2},
            },
            {
                "company": {"company_id": 2, "symbol": "MSFT", "name": "Microsoft"},
                "confidence_counts": {"high": 1},
            },
        ]
        mock_decide_company_purchase.return_value = {
            "company": {"symbol": "AAPL"},
            "recommendation": {"decision": "buy", "confidence": "high"},
        }
        mock_decide_company_option_position.return_value = {
            "company": {"symbol": "AAPL"},
            "recommendation": {"decision": "buy_call", "confidence": "high"},
        }
        mock_apply_deterministic_option_selection.side_effect = lambda result: result

        result = agent_runner_main.run_full_agent_stack_from_existing_data()

        self.assertEqual(result["company_symbols"], ["AAPL"])
        self.assertEqual(
            result["skipped_companies_before_strategist_manager"],
            [
                {
                    "symbol": "MSFT",
                    "company_id": 2,
                    "name": "Microsoft",
                    "high_confidence_article_count": 1,
                    "minimum_required_high_confidence_articles": 2,
                    "window_max_age_days": agent_runner_main.DEFAULT_MAX_ARTICLE_AGE_DAYS,
                    "skip_reason": "insufficient_high_confidence_articles",
                }
            ],
        )
        mock_decide_company_purchase.assert_called_once_with("AAPL")
        mock_decide_company_option_position.assert_called_once()
        option_position_call = mock_decide_company_option_position.call_args
        self.assertEqual(option_position_call.args[0], "AAPL")
        self.assertEqual(
            option_position_call.kwargs["strategist_recommendation"],
            {"decision": "buy", "confidence": "high"},
        )


if __name__ == "__main__":
    unittest.main()
