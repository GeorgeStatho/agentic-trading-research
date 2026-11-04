from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from db_common import get_connection
from market_db_modules.backtesting import counterfactual_trade, record_historical_option_quote
from market_db_modules.bootstrap import initialize_database

try:
    from api_support.context import OPTION_POSITION_SETTINGS
    from api_support.decision_autopsy import counterfactual_for_row
    from trading_support._option_positions_defaults import build_option_exit_rules
    from trading_support._option_positions_market import _resolve_option_exit_thresholds
    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    FLASK_AVAILABLE = False

CONTRACT = "ORCL261204P00123000"
DECISION = {
    "id": 7,
    "symbol": "ORCL",
    "decision_run_at": "2026-10-01T15:00:00+00:00",
    "manager_decision": "put",
    "manager_reason": "Bearish catalysts.",
    "selected_option_symbol": CONTRACT,
}


class CounterfactualTradeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "counterfactual.sqlite3"
        initialize_database(db_path=self.db_path)
        # End-of-day quotes on the contract the manager picked: the entry day, then three closes.
        for quote_at, bid, ask in (
            ("2026-10-01T20:00:00+00:00", 4.9, 5.0),
            ("2026-10-02T20:00:00+00:00", 5.5, 5.6),
            ("2026-10-03T20:00:00+00:00", 8.0, 8.1),
            ("2026-10-06T20:00:00+00:00", 6.0, 6.1),
        ):
            record_historical_option_quote({"option_symbol": CONTRACT, "quote_at": quote_at, "bid_price": bid, "ask_price": ask},
                                           provider="marketdata_eod", db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def simulate(self, **rules):
        with closing(get_connection(self.db_path)) as conn:
            return counterfactual_trade(conn, DECISION, **{"take_profit_pct": 50.0, "stop_loss_pct": -50.0, "max_hold_hours": 24 * 30, **rules})

    def test_buys_at_the_first_ask_and_sells_where_the_take_profit_is_crossed(self):
        trade = self.simulate()

        self.assertEqual(trade["status"], "simulated_closed")
        self.assertEqual(trade["option_symbol"], CONTRACT)
        self.assertTrue(trade["entry_at"].startswith("2026-10-01"))
        self.assertTrue(trade["exit_at"].startswith("2026-10-03"))
        self.assertIn("take_profit_exit", trade["warnings"])
        self.assertGreater(trade["realized_pnl_pct"], 50)
        self.assertEqual(trade["pricing_source"], "observed_quote")

    def test_a_take_profit_never_reached_holds_to_the_last_mark_in_the_window(self):
        trade = self.simulate(take_profit_pct=100.0)

        self.assertTrue(trade["exit_at"].startswith("2026-10-06"))
        self.assertNotIn("take_profit_exit", trade["warnings"])

    def test_the_hold_limit_cuts_the_marks(self):
        trade = self.simulate(take_profit_pct=100.0, max_hold_hours=30)

        self.assertTrue(trade["exit_at"].startswith("2026-10-02"))

    def test_records_nothing(self):
        self.simulate()

        conn = sqlite3.connect(self.db_path)
        try:
            for table in ("decision_evidence_snapshots", "backtest_runs", "backtest_events"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        finally:
            conn.close()



def ledger_row(*, decision="put", executed=False, contract=True):
    """An autopsy ledger row for a blocked ORCL put whose contract the selector had picked."""
    selected = {"symbol": CONTRACT, "contract_type": "put", "strike_price": 123.0, "expiration_date": "2026-12-04",
                "days_to_expiration": 25, "latest_quote": {"bid_price": 4.9, "ask_price": 5.0}} if contract else None
    return {
        "manager_decision_history_id": 7,
        "symbol": "ORCL",
        "trade_executed": 1 if executed else 0,
        "snapshot_json": json.dumps({"symbol": "ORCL", "decision_run_at": DECISION["decision_run_at"], "manager_decision": decision}),
        "manager_input_json": json.dumps({"market_context": {"current_stock_price": {"latest_trade_price": 140.0}}}),
        "manager_output_json": json.dumps({"recommendation": {"decision": decision, "reason": "Bearish."}, "selected_option": selected}),
    }


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class AutopsyCounterfactualTests(CounterfactualTradeTests.__base__):
    setUp = CounterfactualTradeTests.setUp
    tearDown = CounterfactualTradeTests.tearDown

    def counterfactual(self, row, now="2026-11-04T20:00:00+00:00"):
        with closing(get_connection(self.db_path)) as conn:
            return counterfactual_for_row(conn, row, now=datetime.fromisoformat(now))

    def test_prices_a_blocked_call_or_put_under_the_exit_rule_for_its_dte(self):
        rule = _resolve_option_exit_thresholds(
            days_to_expiration=25,
            default_take_profit_pct=OPTION_POSITION_SETTINGS.take_profit_pct,
            default_stop_loss_pct=OPTION_POSITION_SETTINGS.stop_loss_pct,
            default_exit_hours_to_expiration=0.0,
            exit_rules=build_option_exit_rules(OPTION_POSITION_SETTINGS.dte_rules),
        )

        trade = self.counterfactual(ledger_row())

        self.assertEqual(trade["option_symbol"], CONTRACT)
        self.assertEqual((trade["take_profit_pct"], trade["stop_loss_pct"], trade["dte_bucket"]),
                         (rule.take_profit_pct, rule.stop_loss_pct, rule.dte_rule_label))
        self.assertTrue(trade["entry_at"].startswith("2026-10-01"))
        self.assertIsInstance(trade["realized_pnl_pct"], float)
        self.assertIn(trade["exit_reason"], {"take_profit", "stop_loss", "last_mark"})

    def test_says_when_the_holding_window_has_not_ended(self):
        flat = "ORCL261204P00120000"
        for quote_at, bid, ask in (("2026-10-01T20:00:00+00:00", 4.9, 5.0), ("2026-10-02T20:00:00+00:00", 5.0, 5.1)):
            record_historical_option_quote({"option_symbol": flat, "quote_at": quote_at, "bid_price": bid, "ask_price": ask},
                                           provider="marketdata_eod", db_path=self.db_path)
        row = ledger_row()
        output = json.loads(row["manager_output_json"])
        output["selected_option"]["symbol"] = flat
        row["manager_output_json"] = json.dumps(output)

        running = self.counterfactual(row, now="2026-10-03T20:00:00+00:00")
        finished = self.counterfactual(row, now="2026-12-31T20:00:00+00:00")

        self.assertEqual(running["exit_reason"], "last_mark")
        self.assertTrue(running["still_open"])
        self.assertFalse(finished["still_open"])

    def test_nothing_to_price_without_a_blocked_contract(self):
        self.assertIsNone(self.counterfactual(ledger_row(executed=True)))
        self.assertIsNone(self.counterfactual(ledger_row(decision="neither", contract=False)))
        self.assertIsNone(self.counterfactual(ledger_row(contract=False)))


if __name__ == "__main__":
    unittest.main()
