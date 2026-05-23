from __future__ import annotations

import json
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

from agent_builders import strategist_payload as strategist_payload_builder  # noqa: E402
from agent_helpers.manager import build_manager_input  # noqa: E402
from agent_stages.manager import build_manager_prompt  # noqa: E402
from agent_stages.strategist_prompt import build_strategist_prompt  # noqa: E402
from services.option_dte_buckets import TIME_HORIZON_TO_DTE_BUCKET, get_dte_bucket  # noqa: E402


class VerboseTestCase(unittest.TestCase):
    def log_pass(self, message: str) -> None:
        print(f"[PASS] {self.__class__.__name__}.{self._testMethodName}: {message}")


class StrategistPromptContractTests(VerboseTestCase):
    def test_build_strategist_prompt_emits_expected_contract_shape(self):
        payload = {
            "company": {
                "company_id": 3644,
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "historical_price_data": {},
                "trend_quality": {
                    "1m_sharpe": 1.4,
                    "5d_sharpe": 2.1,
                    "3m_sharpe": 1.8,
                },
            },
            "peer_groups": {"industry": {}, "top_companies": []},
            "filters": {"max_age_days": 5},
            "opportunist_rollup": {"company": {"count": 2}},
            "views": {"company": {"count": 2}},
            "supporting_articles": {
                "article_summaries": [{"title": "Example summary"}],
                "full_articles": [{"title": "Example full article"}],
            },
        }

        system_prompt, user_prompt = build_strategist_prompt(payload)
        user_payload = json.loads(user_prompt)

        self.assertIn("investment strategist", system_prompt.lower())
        self.assertEqual(user_payload["company"]["symbol"], "AAPL")
        self.assertEqual(user_payload["company"]["trend_quality"]["1m_sharpe"], 1.4)
        self.assertIn("recommendation", user_payload["required_output"])
        self.assertIn("5d_sharpe", user_payload["task"])
        self.assertIn("supporting context for return smoothness", user_payload["task"])
        recommendation = user_payload["required_output"]["recommendation"]
        self.assertEqual(recommendation["decision"], "trade_candidate|watchlist|do_not_trade")
        self.assertEqual(recommendation["preferred_option_direction"], "call|put|neither")
        self.assertEqual(recommendation["expected_stock_direction"], "up|down|neutral")
        self.assertIn("supporting_articles", user_payload)
        self.log_pass("strategist prompt preserved the expected contract shape and recommendation schema")

    def test_build_strategist_prompt_includes_live_time_horizon_bucket_mapping(self):
        payload = {
            "company": {
                "company_id": 3644,
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "historical_price_data": {},
                "trend_quality": {},
            },
            "peer_groups": {"industry": {}, "top_companies": []},
            "filters": {"max_age_days": 5},
            "opportunist_rollup": {"company": {"count": 2}},
            "views": {"company": {"count": 2}},
            "supporting_articles": {
                "article_summaries": [{"title": "Example summary"}],
                "full_articles": [{"title": "Example full article"}],
            },
        }

        _, user_prompt = build_strategist_prompt(payload)
        task_text = json.loads(user_prompt)["task"]

        for time_horizon, bucket_key in TIME_HORIZON_TO_DTE_BUCKET.items():
            bucket = get_dte_bucket(bucket_key)
            assert bucket is not None
            self.assertIn(f"{time_horizon} for setups that fit {bucket.label}", task_text)
        self.log_pass("strategist prompt time-horizon guidance stayed aligned with the live DTE bucket registry")

    @patch.object(strategist_payload_builder._company_helpers, "_get_company_market_record")
    @patch.object(strategist_payload_builder._company_helpers, "_build_company_price_context")
    def test_serialize_company_scope_attaches_trend_quality_next_to_historical_price_data(
        self,
        mock_build_company_price_context,
        mock_get_company_market_record,
    ):
        mock_get_company_market_record.return_value = {"rating": "A", "market_weight": 3.5, "raw_json": "{}"}
        mock_build_company_price_context.return_value = (
            {"1mo": {"available": True}},
            {
                "source": "yfinance_history",
                "annualized": True,
                "risk_free_rate": 0.0,
                "trading_periods_per_year": 252,
                "5d_sharpe": 2.1,
                "1m_sharpe": 1.4,
                "3m_sharpe": 1.8,
                "5d_return_pct": 8.4,
                "1m_return_pct": 14.6,
                "3m_return_pct": 31.2,
            },
        )

        result = strategist_payload_builder._company_helpers._serialize_company_scope(
            {
                "company_id": 3644,
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "industry_id": 10,
                "industry_key": "consumer_electronics",
                "industry_name": "Consumer Electronics",
                "sector_id": 5,
                "sector_key": "technology",
                "sector_name": "Technology",
            }
        )

        self.assertEqual(result["historical_price_data"]["1mo"]["available"], True)
        self.assertEqual(result["trend_quality"]["5d_sharpe"], 2.1)
        self.assertEqual(result["trend_quality"]["5d_return_pct"], 8.4)
        self.log_pass("company scope now includes strategist-side trend quality alongside historical price data")

    def test_summarize_trend_quality_from_closes_returns_annualized_sharpe(self):
        sharpe_value, return_pct = strategist_payload_builder._summarize_trend_quality_from_closes(
            [100.0, 102.0, 103.0, 105.0, 108.0, 110.0],
            history_period="1mo",
            interval="1d",
        )

        self.assertIsInstance(sharpe_value, float)
        self.assertGreater(sharpe_value, 0.0)
        self.assertAlmostEqual(return_pct, 10.0)
        self.log_pass("trend quality summary computed a positive annualized sharpe from close returns")


class ManagerPayloadContractTests(VerboseTestCase):
    @patch("agent_helpers.manager.list_recent_manager_decision_history")
    @patch("agent_helpers.manager.build_market_context")
    @patch("agent_helpers.manager.build_strategist_input")
    def test_build_manager_input_keeps_expired_pnl_hidden_from_recent_history(
        self,
        mock_build_strategist_input,
        mock_build_market_context,
        mock_list_recent_manager_decision_history,
    ):
        mock_build_strategist_input.return_value = {
            "company": {"company_id": 1, "symbol": "AAPL", "sector_key": "technology"},
            "views": {"company": {"count": 2}},
            "supporting_articles": {"article_summaries": [], "full_articles": []},
            "filters": {"max_age_days": 5},
        }
        mock_build_market_context.return_value = {
            "current_stock_price": {"available": True},
            "option_market": {"available": True, "contract_count": 8},
        }
        mock_list_recent_manager_decision_history.return_value = [
            {
                "decision_run_at": "2026-05-01T14:00:00+00:00",
                "manager_decision": "call",
                "manager_confidence": "medium",
                "target_dte_bucket": "20_30",
                "trade_executed": 1,
                "latest_trade_pnl_pct": None,
                "resolved_outcome_label": None,
                "manager_input_json": json.dumps(
                    {
                        "article_references": [
                            {
                                "article_id": 22,
                                "title": "Older Apple article",
                                "source": "Newswire",
                                "published_at": "2026-04-29T14:00:00+00:00",
                                "article_scope": "company",
                                "evidence_layers": ["company_view"],
                            }
                        ]
                    }
                ),
            }
        ]

        payload = build_manager_input(
            "AAPL",
            start_time=None,
            end_time=None,
            max_age_days=5,
            summary_article_limit=20,
            full_article_limit=5,
            option_expiration_date=None,
            option_expiration_date_gte="2026-05-14",
            option_expiration_date_lte="2026-05-21",
            option_strike_price_gte=None,
            option_strike_price_lte=None,
            option_contract_limit_per_type=6,
        )

        self.assertEqual(len(payload["recent_manager_decision_history"]), 1)
        history_entry = payload["recent_manager_decision_history"][0]
        self.assertIsNone(history_entry["latest_trade_pnl_pct"])
        self.assertEqual(history_entry["resolved_outcome_label"], "")
        self.assertEqual(history_entry["article_references"][0]["title"], "Older Apple article")
        self.log_pass("manager payload preserved expired P/L masking while still carrying prior article references")

    def test_build_manager_prompt_mentions_recent_decision_history_guidance(self):
        payload = {
            "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple Inc."},
            "peer_groups": {"industry": {}, "top_companies": []},
            "opportunist_rollup": {"company": {"count": 2, "top_reasons": ["Fresh demand signal"]}},
            "filters": {"max_age_days": 5},
            "views": {
                "company": {
                    "count": 4,
                    "label": "Company impacts",
                    "agent_conclusions": [
                        {
                            "article_id": 1,
                            "confidence": "medium",
                            "reason": "Medium detail",
                            "impact_direction": "positive",
                            "impact_magnitude": "moderate",
                            "published_at": "2026-05-18T14:00:00+00:00",
                            "title": "Medium signal",
                        },
                        {
                            "article_id": 2,
                            "confidence": "high",
                            "reason": "Highest detail",
                            "impact_direction": "positive",
                            "impact_magnitude": "high",
                            "published_at": "2026-05-20T14:00:00+00:00",
                            "title": "High signal",
                        },
                        {
                            "article_id": 3,
                            "confidence": "low",
                            "reason": "Low detail",
                            "impact_direction": "negative",
                            "impact_magnitude": "low",
                            "published_at": "2026-05-17T14:00:00+00:00",
                            "title": "Low signal",
                        },
                        {
                            "article_id": 4,
                            "confidence": "high",
                            "reason": "Second high detail",
                            "impact_direction": "positive",
                            "impact_magnitude": "moderate",
                            "published_at": "2026-05-19T14:00:00+00:00",
                            "title": "Second high signal",
                        },
                    ],
                }
            },
            "supporting_articles": {
                "article_summaries": [
                    {
                        "article_id": 10,
                        "title": "Fresh Apple article",
                        "summary": "AAPL summary",
                        "source": "Newswire",
                        "source_url": "https://example.com/aapl",
                        "published_at": "2026-05-20T14:00:00+00:00",
                        "article_scope": "company",
                        "evidence_layers": ["company_view"],
                        "agent_signals": [],
                        "body": "Long body that should not be forwarded to the manager prompt payload.",
                    }
                ],
                "full_articles": [
                    {
                        "article_id": 10,
                        "title": "Fresh Apple article",
                        "summary": "AAPL summary",
                        "body": "Long body that should not be forwarded to the manager prompt payload.",
                        "source": "Newswire",
                        "source_url": "https://example.com/aapl",
                        "published_at": "2026-05-20T14:00:00+00:00",
                        "article_scope": "company",
                        "evidence_layers": ["company_view"],
                        "agent_signals": [],
                    }
                ],
            },
            "recent_manager_decision_history": [
                {
                    "decision_run_at": "2026-05-21T14:00:00+00:00",
                    "manager_decision": "call",
                    "manager_confidence": "high",
                    "target_dte_bucket": "20_30",
                    "trade_executed": True,
                    "latest_trade_pnl_pct": 12.5,
                    "resolved_outcome_label": "open_profit",
                    "article_references": [
                        {
                            "article_id": 10,
                            "title": "Fresh Apple article",
                            "source": "Newswire",
                            "published_at": "2026-05-20T14:00:00+00:00",
                            "article_scope": "company",
                            "evidence_layers": ["company_view"],
                        }
                    ],
                }
            ],
            "strategist_recommendation": {
                "decision": "trade_candidate",
                "confidence": "high",
                "preferred_option_direction": "call",
                "expected_stock_direction": "up",
            },
            "market_context": {
                "current_stock_price": {"available": True, "price": 200.0},
                "market_indices": {},
                "sector_etf": {"available": True},
                "account_state": {"available": True, "company_position_state": {"matching_position_count": 0}},
                "option_market": {"available": True, "contract_count": 8, "volatility_summary": {}},
            },
        }

        system_prompt, user_prompt = build_manager_prompt(payload)
        user_payload = json.loads(user_prompt)

        self.assertIn("recent same-company manager decision history", system_prompt)
        self.assertIn("Use opportunist_rollup as the primary summarized evidence layer", system_prompt)
        self.assertIn("opportunist_rollup", user_payload)
        self.assertIn("views_summary", user_payload)
        self.assertIn("context_snapshot", user_payload)
        self.assertIn("recent_manager_decision_history", user_payload)
        self.assertNotIn("peer_groups", user_payload)
        self.assertEqual(len(user_payload["recent_manager_decision_history"]), 1)
        self.assertEqual(
            user_payload["recent_manager_decision_history"][0]["article_references"][0]["title"],
            "Fresh Apple article",
        )
        self.assertNotIn("full_articles", user_payload["supporting_articles"])
        self.assertEqual(user_payload["views_summary"]["company"]["count"], 4)
        self.assertEqual(len(user_payload["views_summary"]["company"]["top_signals"]), 3)
        self.assertEqual(user_payload["views_summary"]["company"]["top_signals"][0]["title"], "High signal")
        self.assertNotIn("agent_conclusions", user_payload["views_summary"]["company"])
        self.assertNotIn("source_url", user_payload["supporting_articles"]["article_summaries"][0])
        self.assertEqual(user_payload["supporting_articles"]["article_summaries"][0]["title"], "Fresh Apple article")
        self.log_pass("manager prompt kept the recent-decision-history guidance and exposed the recent history block")

    @patch("agent_helpers.manager.list_recent_manager_decision_history")
    @patch("agent_helpers.manager.build_market_context")
    @patch("agent_helpers.manager.build_strategist_input")
    def test_build_manager_input_attaches_market_context_and_preserves_core_payload(
        self,
        mock_build_strategist_input,
        mock_build_market_context,
        mock_list_recent_manager_decision_history,
    ):
        mock_build_strategist_input.return_value = {
            "company": {"company_id": 1, "symbol": "AAPL", "sector_key": "technology"},
            "views": {"company": {"count": 3}},
            "supporting_articles": {
                "article_summaries": [
                    {
                        "article_id": 10,
                        "title": "Fresh Apple article",
                        "summary": "AAPL summary",
                        "source": "Newswire",
                        "source_url": "https://example.com/aapl",
                        "published_at": "2026-05-20T14:00:00+00:00",
                        "article_scope": "company",
                        "evidence_layers": ["company_view"],
                        "agent_signals": [],
                    }
                ],
                "full_articles": [],
            },
            "filters": {"max_age_days": 5},
        }
        mock_build_market_context.return_value = {
            "current_stock_price": {"available": True},
            "option_market": {"available": True, "contract_count": 8},
        }
        mock_list_recent_manager_decision_history.return_value = [
            {
                "decision_run_at": "2026-05-21T14:00:00+00:00",
                "manager_decision": "call",
                "manager_confidence": "high",
                "target_dte_bucket": "20_30",
                "trade_executed": 1,
                "latest_trade_pnl_pct": 12.5,
                "resolved_outcome_label": "open_profit",
                "manager_input_json": json.dumps(
                    {
                        "article_references": [
                            {
                                "article_id": 10,
                                "title": "Fresh Apple article",
                                "source": "Newswire",
                                "published_at": "2026-05-20T14:00:00+00:00",
                                "article_scope": "company",
                                "evidence_layers": ["company_view"],
                            }
                        ]
                    }
                ),
            }
        ]

        payload = build_manager_input(
            "AAPL",
            start_time=None,
            end_time=None,
            max_age_days=5,
            summary_article_limit=20,
            full_article_limit=5,
            option_expiration_date=None,
            option_expiration_date_gte="2026-05-14",
            option_expiration_date_lte="2026-05-21",
            option_strike_price_gte=None,
            option_strike_price_lte=None,
            option_contract_limit_per_type=6,
        )

        mock_build_strategist_input.assert_called_once()
        mock_build_market_context.assert_called_once()
        mock_list_recent_manager_decision_history.assert_called_once()
        self.assertEqual(payload["company"]["symbol"], "AAPL")
        self.assertIn("market_context", payload)
        self.assertIn("recent_manager_decision_history", payload)
        self.assertTrue(payload["market_context"]["current_stock_price"]["available"])
        self.assertEqual(payload["market_context"]["option_market"]["contract_count"], 8)
        self.assertEqual(payload["filters"]["max_age_days"], 5)
        self.assertEqual(len(payload["recent_manager_decision_history"]), 1)
        history_entry = payload["recent_manager_decision_history"][0]
        self.assertEqual(history_entry["manager_decision"], "call")
        self.assertEqual(history_entry["latest_trade_pnl_pct"], 12.5)
        self.assertEqual(history_entry["resolved_outcome_label"], "open_profit")
        self.assertEqual(history_entry["article_references"][0]["title"], "Fresh Apple article")
        self.log_pass("manager payload kept the strategist core payload and attached the built market context")


if __name__ == "__main__":
    unittest.main()
