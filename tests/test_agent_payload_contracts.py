from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agent_helpers.manager import build_manager_input  # noqa: E402
from agent_stages.manager_strategist_context import _build_manager_visible_strategist_context  # noqa: E402


class VerboseTestCase(unittest.TestCase):
    def log_pass(self, message: str) -> None:
        print(f"[PASS] {self.__class__.__name__}.{self._testMethodName}: {message}")


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

    def test_manager_strips_strategist_references_outside_its_own_payload(self):
        context = _build_manager_visible_strategist_context(
            {
                "company": {"company_id": 1, "industry_id": 2, "sector_id": 3, "symbol": "AAPL"},
                "grounding": {
                    "company": {"id": 1, "symbol": "AAPL"},
                    "industry": {"id": 2},
                    "sector": {"id": 3},
                    "article_ids": [10],
                },
                "strategist_recommendation": {
                    "decision": "trade_candidate",
                    "confidence": "high",
                    "grounded_company_symbol": "AAPL",
                    "grounded_entity_ids": {"company_id": 1, "industry_id": 2, "sector_id": 3},
                    "evidence_claims": [
                        {
                            "claim_type": "summary",
                            "claim": "Unseen article claim",
                            "evidence_article_ids": [99],
                        }
                    ],
                },
            }
        )

        self.assertEqual(context["decision"], "trade_candidate")
        self.assertNotIn("evidence_claims", context)
        self.assertNotIn("grounded_entity_ids", context)


if __name__ == "__main__":
    unittest.main()
