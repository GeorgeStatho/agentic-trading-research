from __future__ import annotations

import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"

from market_db import (  # noqa: E402
    add_company,
    add_industry,
    add_sector,
    expire_manager_decision_pnl,
    get_latest_open_manager_decision_for_trade_link,
    initialize_database,
    list_decision_autopsy_ledger,
    link_manager_decision_to_trade_execution,
    list_recent_manager_decision_history,
    record_manager_decision_history,
    record_manager_decision_closed_outcome,
    update_manager_decision_latest_pnl,
)
from db_common import get_connection  # noqa: E402


class ManagerDecisionHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "manager_decision_history_test.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")

        sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        industry_id = add_industry(
            sector_id,
            "software-infrastructure",
            "Software Infrastructure",
            db_path=self.db_path,
        )
        self.company_id = add_company(
            industry_id,
            "MSFT",
            "Microsoft Corporation",
            market_weight=10.0,
            db_path=self.db_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_record_and_list_manager_decision_history(self) -> None:
        decision_id = record_manager_decision_history(
            {
                "company_id": self.company_id,
                "symbol": "MSFT",
                "company_name": "Microsoft Corporation",
                "decision_run_at": "2026-05-22T14:00:00+00:00",
                "manager_stage_version": "decision-only-v3",
                "strategist_decision": "trade_candidate",
                "manager_decision": "call",
                "manager_confidence": "high",
                "manager_reason": "Momentum and article support were aligned.",
                "target_dte_bucket": "20_30",
                "selected_option_id": "MSFT260619C00450000",
                "selected_option_symbol": "MSFT260619C00450000",
                "selected_expiration_date": "2026-06-19",
                "selected_strike_price": 450.0,
                "selected_option_source": "deterministic_high_confidence_hybrid_filtered_short_swing",
                "manager_input_json": {"company": {"symbol": "MSFT"}},
                "manager_output_json": {"recommendation": {"decision": "call"}},
            },
            db_path=self.db_path,
        )

        self.assertIsNotNone(decision_id)
        rows = list_recent_manager_decision_history(
            underlying_symbol="MSFT",
            db_path=self.db_path,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], decision_id)
        self.assertEqual(rows[0]["manager_decision"], "call")
        self.assertEqual(rows[0]["manager_confidence"], "high")
        self.assertEqual(rows[0]["selected_option_symbol"], "MSFT260619C00450000")

        ledger_rows = list_decision_autopsy_ledger(db_path=self.db_path)
        self.assertEqual(len(ledger_rows), 1)
        snapshot = json.loads(ledger_rows[0]["snapshot_json"])
        self.assertEqual(ledger_rows[0]["manager_decision_history_id"], decision_id)
        self.assertEqual(snapshot["manager_input"]["company"]["symbol"], "MSFT")
        self.assertEqual(snapshot["manager_output"]["recommendation"]["decision"], "call")

    def test_link_trade_execution_and_update_latest_pnl(self) -> None:
        submitted_at = "2026-05-22T14:05:00+00:00"
        decision_id = record_manager_decision_history(
            {
                "company_id": self.company_id,
                "symbol": "MSFT",
                "company_name": "Microsoft Corporation",
                "decision_run_at": "2026-05-22T14:00:00+00:00",
                "manager_decision": "call",
                "manager_confidence": "high",
                "selected_option_symbol": "MSFT260619C00450000",
            },
            db_path=self.db_path,
        )

        matched_row = get_latest_open_manager_decision_for_trade_link(
            company_id=self.company_id,
            selected_option_symbol="MSFT260619C00450000",
            submitted_at=submitted_at,
            db_path=self.db_path,
        )
        self.assertIsNotNone(matched_row)
        self.assertEqual(matched_row["id"], decision_id)

        linked_id = link_manager_decision_to_trade_execution(
            trade_execution_order_id="order-123",
            trade_execution_record_id=77,
            company_id=self.company_id,
            symbol="MSFT",
            selected_option_symbol="MSFT260619C00450000",
            submitted_at=submitted_at,
            db_path=self.db_path,
        )
        self.assertEqual(linked_id, decision_id)

        updated = update_manager_decision_latest_pnl(
            decision_history_id=decision_id,
            latest_trade_pnl_pct=12.5,
            latest_trade_pnl_updated_at="2026-05-23T14:00:00+00:00",
            pnl_expires_at="2026-12-31T14:00:00+00:00",
            resolved_outcome_label="open_profit",
            db_path=self.db_path,
        )
        self.assertTrue(updated)

        self.assertEqual(list_decision_autopsy_ledger(
            company_id=self.company_id, resolved_only=True, db_path=self.db_path,
        ), [])
        record_manager_decision_closed_outcome(
            decision_history_id=decision_id, realized_pnl_pct=11.0,
            closed_at="2026-05-24T14:00:00+00:00", resolved_outcome_label="closed_profit",
            broker_confirmed=True, db_path=self.db_path,
        )

        resolved_ledger_rows = list_decision_autopsy_ledger(
            company_id=self.company_id,
            resolved_only=True,
            within_days=365,
            db_path=self.db_path,
        )
        self.assertEqual(len(resolved_ledger_rows), 1)
        self.assertEqual(resolved_ledger_rows[0]["manager_decision_history_id"], decision_id)

        rows = list_recent_manager_decision_history(
            underlying_symbol="MSFT",
            db_path=self.db_path,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_executed"], 1)
        self.assertEqual(rows[0]["trade_execution_order_id"], "order-123")
        self.assertEqual(rows[0]["trade_execution_record_id"], 77)
        self.assertEqual(rows[0]["latest_trade_pnl_pct"], 12.5)
        self.assertEqual(rows[0]["resolved_outcome_label"], "closed_profit")

    def test_autopsy_ledger_loads_linked_live_order_details(self) -> None:
        submitted_at = "2026-05-22T14:05:00+00:00"
        decision_id = record_manager_decision_history(
            {
                "company_id": self.company_id,
                "symbol": "MSFT",
                "company_name": "Microsoft Corporation",
                "decision_run_at": "2026-05-22T14:00:00+00:00",
                "manager_decision": "call",
                "manager_confidence": "high",
                "selected_option_symbol": "MSFT260619C00450000",
            },
            db_path=self.db_path,
        )
        with get_connection(self.db_path) as conn:
            execution_id = conn.execute(
                """
                INSERT INTO option_trade_executions (
                    company_id, order_id, underlying_symbol, option_symbol, decision,
                    expiration_date, order_qty, paper, order_status, order_side, submitted_at
                ) VALUES (?, 'live-order-123', 'MSFT', 'MSFT260619C00450000', 'call',
                          '2026-06-19', 2, 0, 'filled', 'buy', ?)
                """,
                (self.company_id, submitted_at),
            ).lastrowid
        link_manager_decision_to_trade_execution(
            trade_execution_order_id="live-order-123",
            trade_execution_record_id=execution_id,
            company_id=self.company_id,
            symbol="MSFT",
            selected_option_symbol="MSFT260619C00450000",
            submitted_at=submitted_at,
            db_path=self.db_path,
        )
        update_manager_decision_latest_pnl(
            decision_history_id=decision_id,
            latest_trade_pnl_pct=-3.5,
            latest_trade_pnl_updated_at="2026-05-23T14:00:00+00:00",
            pnl_expires_at="2026-12-31T14:00:00+00:00",
            resolved_outcome_label="closed_loss",
            db_path=self.db_path,
        )

        rows = list_decision_autopsy_ledger(company_id=self.company_id, db_path=self.db_path)
        record = next(row for row in rows if row["manager_decision_history_id"] == decision_id)
        self.assertEqual(record["execution_paper"], 0)
        self.assertEqual(record["execution_order_status"], "filled")
        self.assertEqual(record["execution_option_symbol"], "MSFT260619C00450000")

    def test_expire_manager_decision_pnl_clears_stale_pnl_without_deleting_history(self) -> None:
        decision_id = record_manager_decision_history(
            {
                "company_id": self.company_id,
                "symbol": "MSFT",
                "company_name": "Microsoft Corporation",
                "decision_run_at": "2026-05-01T14:00:00+00:00",
                "manager_decision": "call",
                "manager_confidence": "medium",
                "selected_option_symbol": "MSFT260619C00450000",
                "trade_executed": True,
                "latest_trade_pnl_pct": -8.0,
                "latest_trade_pnl_updated_at": "2026-05-05T14:00:00+00:00",
                "pnl_expires_at": "2026-05-10T14:00:00+00:00",
            },
            db_path=self.db_path,
        )

        expired_count = expire_manager_decision_pnl(
            now="2026-05-22T14:00:00+00:00",
            db_path=self.db_path,
        )
        self.assertEqual(expired_count, 1)

        rows = list_recent_manager_decision_history(
            underlying_symbol="MSFT",
            db_path=self.db_path,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], decision_id)
        self.assertIsNone(rows[0]["latest_trade_pnl_pct"])
        self.assertIsNone(rows[0]["latest_trade_pnl_updated_at"])

        rows_with_expired = list_recent_manager_decision_history(
            underlying_symbol="MSFT",
            include_expired_pnl=True,
            db_path=self.db_path,
        )
        self.assertEqual(len(rows_with_expired), 1)
        self.assertEqual(rows_with_expired[0]["latest_trade_pnl_pct"], -8.0)

    def test_expired_history_hides_outcome_label_by_default_but_can_restore_it(self) -> None:
        record_manager_decision_history(
            {
                "company_id": self.company_id,
                "symbol": "MSFT",
                "company_name": "Microsoft Corporation",
                "decision_run_at": "2026-05-01T14:00:00+00:00",
                "manager_decision": "call",
                "manager_confidence": "medium",
                "selected_option_symbol": "MSFT260619C00450000",
                "trade_executed": True,
                "latest_trade_pnl_pct": 6.25,
                "latest_trade_pnl_updated_at": "2026-05-05T14:00:00+00:00",
                "pnl_expires_at": "2026-05-10T14:00:00+00:00",
                "resolved_outcome_label": "managed_exit_submitted_profit",
            },
            db_path=self.db_path,
        )

        expire_manager_decision_pnl(
            now="2026-05-22T14:00:00+00:00",
            db_path=self.db_path,
        )

        masked_rows = list_recent_manager_decision_history(
            underlying_symbol="MSFT",
            db_path=self.db_path,
        )
        self.assertEqual(len(masked_rows), 1)
        self.assertIsNone(masked_rows[0]["latest_trade_pnl_pct"])
        self.assertIsNone(masked_rows[0]["resolved_outcome_label"])

        unmasked_rows = list_recent_manager_decision_history(
            underlying_symbol="MSFT",
            include_expired_pnl=True,
            db_path=self.db_path,
        )
        self.assertEqual(len(unmasked_rows), 1)
        self.assertEqual(unmasked_rows[0]["latest_trade_pnl_pct"], 6.25)
        self.assertEqual(unmasked_rows[0]["resolved_outcome_label"], "managed_exit_submitted_profit")


if __name__ == "__main__":
    unittest.main()
