from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from market_db_modules.history_records import record_manager_decision_history
from news_db_modules.strategist_runs import record_strategist_run
from market_db_modules.entities import add_company, add_industry, add_sector


try:
    from api_support.dashboard import _load_recent_company_decision_rows, build_company_decisions_payload

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_company_decisions_payload = None
    FLASK_AVAILABLE = False


APPLE_MANAGER_INPUT = {
    "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple Inc."},
    "strategist_recommendation": {
        "decision": "buy",
        "confidence": "high",
        "summary": "Momentum and sentiment support upside.",
        "thesis": ["Revenue quality remains strong."],
        "risks": ["Valuation is elevated."],
    },
    "article_references": [
        {
            "article_id": 101,
            "title": "Apple catalyst",
            "source": "Newswire",
            "published_at": "2026-05-21T09:00:00+00:00",
            "article_scope": "company",
            "evidence_layers": ["company_view"],
        }
    ],
    "market_context": {
        "option_market": {"contracts": [{"symbol": f"AAPL-{index}"} for index in range(50)]},
        "underlying_price_history": {
            "available": True,
            "source": "alpaca_stock_bars",
            "feed": "iex",
            "close_count": 60,
            "error": "",
            "bars": [{"close": 190.0}] * 60,
        },
    },
}
APPLE_MANAGER_OUTPUT = {
    "recommendation": {
        "decision": "call",
        "confidence": "medium",
        "reason": "Bullish setup with defined risk.",
        "target_dte_bucket": "30_45",
        "selected_option_id": "AAPL260619C00190000",
        "selected_expiration_date": "2026-06-19",
        "selected_strike_price": "190",
        "selected_option_source": "deterministic_selector",
        "selection_debug": {"option_contract_count": 50},
    },
    "entry_gate": {"allowed": True, "reason_codes": []},
}


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class DashboardCompanyDecisionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "company-decisions.db"
        initialize_market_database(db_path=self.db_path)
        initialize_news_database(db_path=self.db_path)
        db_patch = patch("api_support.dashboard.DB_PATH", self.db_path)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.now = datetime.now(timezone.utc)

    def _hours_ago(self, hours: float) -> str:
        return (self.now - timedelta(hours=hours)).isoformat()

    def _record_decision(self, *, symbol: str = "AAPL", hours_ago: float = 1, **overrides) -> int:
        history = {
            "symbol": symbol,
            "company_name": "Apple Inc.",
            "decision_run_at": self._hours_ago(hours_ago),
            "strategist_decision": "buy",
            "manager_decision": "call",
            "manager_confidence": "medium",
            "manager_reason": "Bullish setup with defined risk.",
            "target_dte_bucket": "30_45",
            "selected_option_id": "AAPL260619C00190000",
            "selected_option_symbol": "AAPL260619C00190000",
            "selected_expiration_date": "2026-06-19",
            "selected_strike_price": 190,
            "selected_option_source": "deterministic_selector",
            "trade_executed": True,
            "trade_execution_order_id": "order-123",
            "latest_trade_pnl_pct": 12.5,
            "latest_trade_pnl_updated_at": self._hours_ago(0.5),
            "pnl_expires_at": (self.now + timedelta(days=5)).isoformat(),
            "resolved_outcome_label": "open_profit",
            "manager_input_json": APPLE_MANAGER_INPUT,
            "manager_output_json": APPLE_MANAGER_OUTPUT,
            **overrides,
        }
        return record_manager_decision_history(history, db_path=self.db_path)

    def _add_company(self) -> int:
        sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        industry_id = add_industry(sector_id, "consumer-electronics", "Consumer Electronics", db_path=self.db_path)
        return add_company(industry_id, "AAPL", "Apple Inc.", db_path=self.db_path)

    def _record_strategist_run(self, company_id: int, *, status: str, hours_ago: float) -> None:
        record_strategist_run(
            company_id=company_id,
            model="test-model",
            prompt_version="v1",
            input_article_ids=[1, 2, 3, 4],
            input_payload={"articles": []},
            output_payload=None,
            status=status,
            error_message="Strategist evidence claim must cite at least one article ID." if status == "failed" else None,
            created_at=self._hours_ago(hours_ago),
            db_path=self.db_path,
        )

    def test_company_decisions_read_manager_history_from_the_database(self):
        decision_id = self._record_decision()

        payload = build_company_decisions_payload()

        self.assertEqual(payload["company_count"], 1)
        company = payload["companies"][0]
        self.assertEqual(company["symbol"], "AAPL")
        self.assertEqual(company["name"], "Apple Inc.")
        self.assertEqual(company["strategist"]["decision"], "buy")
        self.assertEqual(company["strategist"]["summary"], "Momentum and sentiment support upside.")
        self.assertEqual(company["strategist"]["risks"], ["Valuation is elevated."])
        self.assertEqual(company["manager"]["decision"], "call")
        self.assertEqual(company["manager"]["target_dte_bucket"], "30_45")
        self.assertEqual(company["manager"]["manager_decision_history_id"], decision_id)
        self.assertEqual(company["manager"]["selected_strike_price"], 190.0)
        self.assertEqual(company["manager"]["entry_gate"], {"allowed": True, "reason_codes": []})
        self.assertEqual(company["diagnostics"]["historical_bars"]["feed"], "iex")
        self.assertTrue(company["diagnostics"]["historical_bars"]["available"])
        history = company["manager_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["trade_execution_order_id"], "order-123")
        self.assertEqual(history[0]["latest_trade_pnl_pct"], 12.5)
        self.assertEqual(history[0]["article_references"][0]["title"], "Apple catalyst")

    def test_manager_json_is_trimmed_to_the_fields_the_page_shows(self):
        self._record_decision()

        row = _load_recent_company_decision_rows()[0]

        manager_input = json.loads(row["manager_input_json"])
        manager_output = json.loads(row["manager_output_json"])
        self.assertNotIn("option_market", manager_input["market_context"])
        self.assertNotIn("bars", manager_input["market_context"]["underlying_price_history"])
        self.assertNotIn("selection_debug", manager_output["recommendation"])

    def test_manager_history_keeps_the_three_latest_decisions_per_company(self):
        decision_ids = [self._record_decision(hours_ago=hours) for hours in (5, 4, 3, 2, 1)]

        payload = build_company_decisions_payload()

        history_ids = [item["id"] for item in payload["companies"][0]["manager_history"]]
        newest_three = list(reversed(decision_ids[-3:]))
        self.assertEqual(history_ids, newest_three)

    def test_expired_pnl_is_masked(self):
        self._record_decision(pnl_expires_at=self._hours_ago(1))

        history = build_company_decisions_payload()["companies"][0]["manager_history"][0]

        self.assertIsNone(history["latest_trade_pnl_pct"])

    def test_symbol_falls_back_to_the_manager_input_company(self):
        self._record_decision(symbol=None)

        payload = build_company_decisions_payload()

        self.assertEqual([company["symbol"] for company in payload["companies"]], ["AAPL"])

    def test_malformed_manager_json_does_not_hide_other_companies(self):
        self._record_decision()
        with closing(get_connection(self.db_path)) as conn, conn:
            decision_id = conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at) VALUES ('MSFT', ?)",
                (self._hours_ago(2),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, manager_output_json)
                VALUES (?, 'not json', '{')
                """,
                (decision_id,),
            )

        payload = build_company_decisions_payload()

        self.assertEqual(sorted(company["symbol"] for company in payload["companies"]), ["AAPL", "MSFT"])

    def test_company_decisions_returns_empty_payload_when_db_history_is_missing(self):
        payload = build_company_decisions_payload()

        self.assertEqual(payload["company_count"], 0)
        self.assertEqual(payload["last_updated_at"], "")
        self.assertEqual(payload["companies"], [])

    def test_company_decisions_surface_historical_bar_errors(self):
        self._record_decision(
            manager_input_json={
                "market_context": {
                    "underlying_price_history": {
                        "available": False,
                        "source": "alpaca_stock_bars",
                        "feed": "iex",
                        "close_count": 0,
                        "error": "Alpaca request timed out",
                    }
                }
            },
        )

        diagnostics = build_company_decisions_payload()["companies"][0]["diagnostics"]

        self.assertEqual(diagnostics["historical_bars"]["error"], "Alpaca request timed out")
        self.assertFalse(diagnostics["historical_bars"]["available"])

    def test_latest_failed_strategist_run_is_surfaced(self):
        company_id = self._add_company()
        self._record_strategist_run(company_id, status="completed", hours_ago=3)
        self._record_strategist_run(company_id, status="failed", hours_ago=1)

        validation = build_company_decisions_payload()["companies"][0]["diagnostics"]["strategist_validation"]

        self.assertEqual(validation["status"], "failed")
        self.assertEqual(validation["input_article_count"], 4)

    def test_newer_strategist_run_clears_an_earlier_failure(self):
        company_id = self._add_company()
        self._record_decision(company_id=company_id)
        self._record_strategist_run(company_id, status="failed", hours_ago=3)
        self._record_strategist_run(company_id, status="completed", hours_ago=1)

        validation = build_company_decisions_payload()["companies"][0]["diagnostics"]["strategist_validation"]

        self.assertEqual(validation, {})


if __name__ == "__main__":
    unittest.main()
