from __future__ import annotations

import unittest
from unittest.mock import patch


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
    def test_company_decisions_use_db_history_as_primary_source(self):
        recent_history_rows = [
            {
                "id": 77,
                "company_id": 1,
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "decision_run_at": "2026-05-21T14:30:00+00:00",
                "strategist_decision": "buy",
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
                "manager_input_json": (
                    '{"company":{"company_id":1,"symbol":"AAPL","name":"Apple Inc."},'
                    '"strategist_recommendation":{"decision":"buy","confidence":"high",'
                    '"summary":"Momentum and sentiment support upside.",'
                    '"thesis":["Revenue quality remains strong."],'
                    '"risks":["Valuation is elevated."]},'
                    '"article_references":[{"article_id":101,"title":"Apple catalyst","source":"Newswire",'
                    '"published_at":"2026-05-21T09:00:00+00:00","article_scope":"company",'
                    '"evidence_layers":["company_view"]}]}'
                ),
                "manager_output_json": (
                    '{"recommendation":{"decision":"call","confidence":"medium",'
                    '"reason":"Bullish setup with defined risk.","target_dte_bucket":"30_45",'
                    '"selected_option_id":"AAPL260619C00190000",'
                    '"selected_expiration_date":"2026-06-19","selected_strike_price":"190",'
                    '"selected_option_source":"deterministic_selector"}}'
                ),
            }
        ]

        with (
            patch("api_support.dashboard.list_recent_manager_decision_history", return_value=recent_history_rows),
            patch("api_support.dashboard._load_latest_strategist_run_statuses", return_value={}),
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

    def test_company_decisions_returns_empty_payload_when_db_history_is_missing(self):
        with (
            patch("api_support.dashboard.list_recent_manager_decision_history", return_value=[]),
            patch("api_support.dashboard._load_latest_strategist_run_statuses", return_value={}),
        ):
            payload = build_company_decisions_payload()

        self.assertEqual(payload["company_count"], 0)
        self.assertEqual(payload["last_updated_at"], "")
        self.assertEqual(payload["companies"], [])

    def test_company_decisions_surface_historical_bar_and_strategist_validation_errors(self):
        recent_history_rows = [
            {
                "id": 77,
                "company_id": 1,
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "decision_run_at": "2026-05-21T14:30:00+00:00",
                "strategist_decision": "watchlist",
                "manager_decision": "neither",
                "manager_input_json": (
                    '{"market_context":{"underlying_price_history":'
                    '{"available":false,"source":"alpaca_stock_bars","feed":"iex",'
                    '"close_count":0,"error":"Alpaca request timed out"}}}'
                ),
                "manager_output_json": "{}",
            }
        ]
        strategist_statuses = {
            1: {
                "company_id": 1,
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "status": "failed",
                "error_message": "Strategist evidence claim must cite at least one article ID.",
                "input_article_count": 4,
                "created_at": "2026-05-21T14:35:00+00:00",
            }
        }

        with (
            patch("api_support.dashboard.list_recent_manager_decision_history", return_value=recent_history_rows),
            patch("api_support.dashboard._load_latest_strategist_run_statuses", return_value=strategist_statuses),
        ):
            payload = build_company_decisions_payload()

        diagnostics = payload["companies"][0]["diagnostics"]
        self.assertEqual(diagnostics["historical_bars"]["error"], "Alpaca request timed out")
        self.assertEqual(diagnostics["historical_bars"]["feed"], "iex")
        self.assertEqual(diagnostics["strategist_validation"]["status"], "failed")
        self.assertEqual(diagnostics["strategist_validation"]["input_article_count"], 4)


if __name__ == "__main__":
    unittest.main()
