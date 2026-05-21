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
                    "company": {"symbol": "AAPL", "name": "Apple Inc."},
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

        with patch("api_support.dashboard.read_json_payload", return_value=agent_output):
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
        self.assertEqual(company["manager"]["selected_option_id"], "AAPL260619C00190000")
        self.assertEqual(company["manager"]["selected_strike_price"], 190.0)

    def test_company_decisions_returns_empty_payload_when_output_is_missing(self):
        with patch("api_support.dashboard.read_json_payload", return_value=None):
            payload = build_company_decisions_payload()

        self.assertEqual(payload["company_count"], 0)
        self.assertEqual(payload["last_updated_at"], "")
        self.assertEqual(payload["companies"], [])


if __name__ == "__main__":
    unittest.main()
