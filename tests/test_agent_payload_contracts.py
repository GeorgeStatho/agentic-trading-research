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

from agent_helpers.manager import build_manager_input  # noqa: E402
from agent_stages.strategist_prompt import build_strategist_prompt  # noqa: E402


class StrategistPromptContractTests(unittest.TestCase):
    def test_build_strategist_prompt_emits_expected_contract_shape(self):
        payload = {
            "company": {
                "company_id": 3644,
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "historical_price_data": {},
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
        self.assertIn("recommendation", user_payload["required_output"])
        recommendation = user_payload["required_output"]["recommendation"]
        self.assertEqual(recommendation["decision"], "trade_candidate|watchlist|do_not_trade")
        self.assertEqual(recommendation["preferred_option_direction"], "call|put|neither")
        self.assertEqual(recommendation["expected_stock_direction"], "up|down|neutral")
        self.assertIn("supporting_articles", user_payload)


class ManagerPayloadContractTests(unittest.TestCase):
    @patch("agent_helpers.manager.build_market_context")
    @patch("agent_helpers.manager.build_strategist_input")
    def test_build_manager_input_attaches_market_context_and_preserves_core_payload(
        self,
        mock_build_strategist_input,
        mock_build_market_context,
    ):
        mock_build_strategist_input.return_value = {
            "company": {"symbol": "AAPL", "sector_key": "technology"},
            "views": {"company": {"count": 3}},
            "supporting_articles": {"article_summaries": [], "full_articles": []},
            "filters": {"max_age_days": 5},
        }
        mock_build_market_context.return_value = {
            "current_stock_price": {"available": True},
            "option_market": {"available": True, "contract_count": 8},
        }

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
        self.assertEqual(payload["company"]["symbol"], "AAPL")
        self.assertIn("market_context", payload)
        self.assertTrue(payload["market_context"]["current_stock_price"]["available"])
        self.assertEqual(payload["market_context"]["option_market"]["contract_count"], 8)
        self.assertEqual(payload["filters"]["max_age_days"], 5)


if __name__ == "__main__":
    unittest.main()
