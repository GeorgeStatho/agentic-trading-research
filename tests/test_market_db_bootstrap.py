from __future__ import annotations

from contextlib import closing
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database


class MarketSetupFingerprintTests(unittest.TestCase):
    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db_path = Path(temp_dir.name) / "market.sqlite3"
        initialize_database(db_path=self.db_path)
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("ALTER TABLE manager_decision_history DROP COLUMN exit_reason")

    def manager_history_columns(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            return {row["name"] for row in conn.execute("PRAGMA table_info(manager_decision_history)")}

    def test_database_set_up_by_the_same_files_is_left_alone(self):
        initialize_database(db_path=self.db_path)

        self.assertNotIn("exit_reason", self.manager_history_columns())

    def test_database_set_up_by_other_files_is_migrated(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE schema_bootstrap SET fingerprint = 'older-setup'")

        initialize_database(db_path=self.db_path)

        self.assertIn("exit_reason", self.manager_history_columns())

    def test_setup_snapshots_decisions_that_predate_the_autopsy_ledger(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            decision_id = conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at) VALUES ('AAPL', '2026-06-01T14:00:00+00:00')"
            ).lastrowid
            conn.execute("UPDATE schema_bootstrap SET fingerprint = 'older-setup'")

        initialize_database(db_path=self.db_path)

        with closing(get_connection(self.db_path)) as conn, conn:
            ledger_ids = [
                row["manager_decision_history_id"]
                for row in conn.execute("SELECT manager_decision_history_id FROM decision_autopsy_ledger")
            ]
        self.assertEqual(ledger_ids, [decision_id])

    def _simulate_payloads_written_by_older_code(self) -> tuple[int, int]:
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("ALTER TABLE manager_decision_history ADD COLUMN manager_input_json TEXT")
            conn.execute("ALTER TABLE manager_decision_history ADD COLUMN manager_output_json TEXT")
            inline_id = conn.execute(
                """
                INSERT INTO manager_decision_history (symbol, decision_run_at, manager_input_json, manager_output_json)
                VALUES ('AAPL', '2026-06-01T14:00:00+00:00', '{"company": {"symbol": "AAPL"}}', '{"recommendation": {}}')
                """
            ).lastrowid
            ledger_only_id = conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at) VALUES ('MSFT', '2026-06-01T15:00:00+00:00')"
            ).lastrowid
            for decision_id, manager_input in ((inline_id, {"company": {"symbol": "AAPL"}}), (ledger_only_id, {"company": {"symbol": "MSFT"}})):
                conn.execute(
                    """
                    INSERT INTO decision_autopsy_ledger (manager_decision_history_id, decision_run_at, snapshot_json)
                    VALUES (?, '2026-06-01T14:00:00+00:00', ?)
                    """,
                    (decision_id, json.dumps({"version": "decision-autopsy-v1", "manager_input": manager_input, "manager_output": {}})),
                )
            conn.execute("UPDATE schema_bootstrap SET fingerprint = 'older-setup'")
        initialize_database(db_path=self.db_path)
        return inline_id, ledger_only_id

    def test_setup_moves_inline_manager_json_into_the_payload_table(self):
        inline_id, ledger_only_id = self._simulate_payloads_written_by_older_code()

        with closing(get_connection(self.db_path)) as conn, conn:
            payloads = {
                row["manager_decision_history_id"]: json.loads(row["manager_input_json"])["company"]["symbol"]
                for row in conn.execute("SELECT * FROM manager_decision_payloads")
            }
        self.assertNotIn("manager_input_json", self.manager_history_columns())
        self.assertEqual(payloads, {inline_id: "AAPL", ledger_only_id: "MSFT"})

    def test_setup_strips_payload_copies_from_autopsy_snapshots(self):
        self._simulate_payloads_written_by_older_code()

        with closing(get_connection(self.db_path)) as conn, conn:
            snapshots = [json.loads(row["snapshot_json"]) for row in conn.execute("SELECT snapshot_json FROM decision_autopsy_ledger")]
        self.assertEqual(snapshots, [{"version": "decision-autopsy-v2"}] * 2)

    def test_executed_decisions_have_their_own_date_index(self):
        with closing(get_connection(self.db_path)) as conn, conn:
            index_names = {row["name"] for row in conn.execute("PRAGMA index_list(manager_decision_history)")}

        self.assertIn("idx_manager_decision_history_executed_run_at", index_names)


if __name__ == "__main__":
    unittest.main()
