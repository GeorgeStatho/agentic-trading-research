from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db_common import use_database
from market_db_modules.bootstrap import initialize_database
from market_db_modules.trade_executions import record_option_trade_execution

try:
    from api_support import trades
    from api_support.context import OPTION_POSITION_SETTINGS
    from trading_support._option_positions_defaults import build_option_exit_rules
    from trading_support._option_positions_market import _resolve_option_exit_thresholds
    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    FLASK_AVAILABLE = False


def execution(**overrides):
    """Shaped like the pipeline's trade-execution output for ORCL on Nov 4, 2026."""
    return {
        "symbol": "ORCL",
        "name": "Oracle Corporation",
        "decision": "put",
        "confidence": "medium",
        "reason": "Fresh bearish catalysts.",
        "submitted": True,
        "error": "",
        "order_qty": 1,
        "strategist_recommendation": {"decision": "trade_candidate", "confidence": "medium"},
        "selected_option_symbol": "ORCL261204P00123000",
        "selected_expiration_date": "2026-12-04",
        "selected_strike_price": 123.0,
        "selected_option": {"symbol": "ORCL261204P00123000", "contract_type": "put", "expiration_date": "2026-12-04", "strike_price": 123.0, "days_to_expiration": 25},
        "order": {"id": "orcl-entry", "symbol": "ORCL261204P00123000", "side": "buy", "status": "filled", "qty": 1, "submitted_at": "2026-11-04T20:40:16+00:00"},
        **overrides,
    }


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class TradeExplanationPayloadTests(unittest.TestCase):
    def payload(self, executions, *, recorded=()):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "explain.sqlite3"
            initialize_database(db_path=db_path)
            for row, fill_price in recorded:
                record_option_trade_execution(row, paper=True, db_path=db_path)
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute("UPDATE option_trade_executions SET fill_price = ? WHERE order_id = ?", (fill_price, row["order"]["id"]))
                    conn.commit()
                finally:
                    conn.close()
            output = root / "trade_execution_output.json"
            output.write_text(json.dumps({"ran_at": "2026-11-04T21:15:58", "executions": executions}), encoding="utf-8")
            with use_database(db_path), \
                    patch.object(trades, "TRADE_EXECUTION_OUTPUT_PATH", output), \
                    patch.object(trades, "SELECTED_OPTIONS_OUTPUT_PATH", root / "missing.json"):
                return trades.build_trade_explanation_payload()

    def test_a_submitted_trade_names_the_company_strategist_contract_and_fill(self):
        payload = self.payload([execution()], recorded=[(execution(), 7.45)])

        self.assertEqual(payload["company_name"], "Oracle Corporation")
        self.assertEqual(payload["strategist_decision"], "trade_candidate")
        self.assertIsNone(payload["stopped_at"])
        self.assertEqual(payload["contract"], {"symbol": "ORCL261204P00123000", "type": "put", "expiration_date": "2026-12-04", "dte": 25, "strike": 123.0})
        self.assertEqual(payload["quantity"], 1)
        self.assertEqual(payload["fill_price"], 7.45)

    def test_the_exit_plan_is_the_one_the_option_manager_applies_at_that_dte(self):
        expected = _resolve_option_exit_thresholds(
            days_to_expiration=25,
            default_take_profit_pct=OPTION_POSITION_SETTINGS.take_profit_pct,
            default_stop_loss_pct=OPTION_POSITION_SETTINGS.stop_loss_pct,
            default_exit_hours_to_expiration=0.0,
            exit_rules=build_option_exit_rules(OPTION_POSITION_SETTINGS.dte_rules),
        )

        plan = self.payload([execution()])["exit_plan"]

        self.assertEqual(plan, {"take_profit_pct": expected.take_profit_pct, "stop_loss_pct": expected.stop_loss_pct, "dte_bucket": expected.dte_rule_label})

    def test_a_blocked_call_or_put_stopped_at_a_safeguard(self):
        payload = self.payload([execution(submitted=False, error="portfolio guardrail: premium cap", order=None)])

        self.assertEqual(payload["stopped_at"], "safeguard")
        self.assertIsNone(payload["fill_price"])

    def test_a_no_trade_names_who_stopped_it(self):
        by_manager = self.payload([execution(decision="neither", submitted=False, selected_option=None, order=None)])
        by_strategist = self.payload([execution(decision="neither", submitted=False, selected_option=None, order=None,
                                                strategist_recommendation={"decision": "not_actionable"})])

        self.assertEqual(by_manager["stopped_at"], "manager")
        self.assertIsNone(by_manager["contract"])
        self.assertIsNone(by_manager["exit_plan"])
        self.assertEqual(by_strategist["stopped_at"], "strategist")

    def test_names_the_last_contract_actually_traded(self):
        earlier = execution(order={"id": "tsla-entry", "symbol": "TSLA261127C00415000", "side": "buy", "status": "filled", "qty": 1, "submitted_at": "2026-10-29T15:21:54+00:00"},
                            selected_option_symbol="TSLA261127C00415000", symbol="TSLA")
        exit_order = execution(order={"id": "tsla-exit", "symbol": "TSLA261127C00415000", "side": "sell", "status": "filled", "qty": 1, "submitted_at": "2026-10-30T13:38:34+00:00"},
                               selected_option_symbol="TSLA261127C00415000", symbol="TSLA")

        payload = self.payload([execution(decision="neither", submitted=False, selected_option=None, order=None)],
                               recorded=[(earlier, 13.45), (exit_order, 7.5)])

        self.assertEqual(payload["last_traded_contract"], "TSLA261127C00415000")


if __name__ == "__main__":
    unittest.main()
