from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

try:
    from api_support.dashboard import build_company_decisions_payload

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_company_decisions_payload = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class DashboardCompanyDecisionsTests(unittest.TestCase):
    def test_company_decisions_merge_strategist_and_manager_results_by_symbol(self):
        agent_output = {
            "ran_at": "2026-05-21T14:30:00+00:00",
            "strategist": [
                {
                    "company": {"symbol": "AAPL", "name": "Apple Inc."},
                    "recommendation": {
                        "decision": "buy",
                        "confidence": "high",
                        "summary": "Momentum and sentiment support upside.",
                        "thesis": ["Revenue quality remains strong."],
                        "risks": ["Valuation is elevated."],
                    },
                }
            ],
            "manager": [
                {
                    "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple Inc."},
                    "manager_decision_history_id": 77,
                    "recommendation": {
                        "decision": "call",
                        "confidence": "medium",
                        "reason": "Bullish setup with defined risk.",
                        "target_dte_bucket": "30_45",
                        "selected_option_id": "AAPL260619C00190000",
                        "selected_expiration_date": "2026-06-19",
                        "selected_strike_price": "190",
                        "selected_option_source": "deterministic_selector",
                    },
                }
            ],
        }
        recent_history_rows = [
            {
                "id": 77,
                "decision_run_at": "2026-05-21T14:30:00+00:00",
                "manager_decision": "call",
                "manager_confidence": "medium",
                "manager_reason": "Bullish setup with defined risk.",
                "target_dte_bucket": "30_45",
                "selected_option_id": "AAPL260619C00190000",
                "selected_option_symbol": "AAPL260619C00190000",
                "selected_expiration_date": "2026-06-19",
                "selected_strike_price": "190",
                "selected_option_source": "deterministic_selector",
                "trade_executed": 1,
                "trade_execution_order_id": "order-123",
                "trade_execution_record_id": 22,
                "latest_trade_pnl_pct": 12.5,
                "latest_trade_pnl_updated_at": "2026-05-22T15:00:00+00:00",
                "pnl_expires_at": "2026-06-05T15:00:00+00:00",
                "resolved_outcome_label": "open_profit",
                "manager_input_json": '{"article_references":[{"article_id":101,"title":"Apple catalyst","source":"Newswire","published_at":"2026-05-21T09:00:00+00:00","article_scope":"company","evidence_layers":["company_view"]}]}',
            }
        ]

        with (
            patch("api_support.dashboard.read_json_payload", return_value=agent_output),
            patch("api_support.dashboard.list_recent_manager_decision_history", return_value=recent_history_rows),
        ):
            payload = build_company_decisions_payload()

        self.assertEqual(payload["company_count"], 1)
        self.assertEqual(payload["last_updated_at"], "2026-05-21T14:30:00+00:00")
        company = payload["companies"][0]
        self.assertEqual(company["symbol"], "AAPL")
        self.assertEqual(company["name"], "Apple Inc.")
        self.assertEqual(company["strategist"]["decision"], "buy")
        self.assertEqual(company["strategist"]["summary"], "Momentum and sentiment support upside.")
        self.assertEqual(company["manager"]["decision"], "call")
        self.assertEqual(company["manager"]["target_dte_bucket"], "30_45")
        self.assertEqual(company["manager"]["manager_decision_history_id"], 77)
        self.assertEqual(company["manager"]["selected_option_id"], "AAPL260619C00190000")
        self.assertEqual(company["manager"]["selected_strike_price"], 190.0)
        self.assertEqual(len(company["manager_history"]), 1)
        self.assertEqual(company["manager_history"][0]["trade_execution_order_id"], "order-123")
        self.assertEqual(company["manager_history"][0]["latest_trade_pnl_pct"], 12.5)
        self.assertEqual(company["manager_history"][0]["article_references"][0]["title"], "Apple catalyst")

    def test_company_decisions_returns_empty_payload_when_output_is_missing(self):
        with patch("api_support.dashboard.read_json_payload", return_value=None):
            payload = build_company_decisions_payload()

        self.assertEqual(payload["company_count"], 0)
        self.assertEqual(payload["last_updated_at"], "")
        self.assertEqual(payload["companies"], [])


if __name__ == "__main__":
    unittest.main()
