"""Each decision autopsy shows how its trade did against SPY and the sector."""
from __future__ import annotations

from contextlib import closing
import unittest

from db_common import get_connection
from market_db_modules.history_records import record_manager_decision_history
from tests.db_fixtures import temporary_database


class DecisionAutopsyBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = temporary_database(self)
        try:
            from api_support.decision_autopsy import build_decision_autopsy_payload
        except ModuleNotFoundError as exc:  # the API package imports Flask at module scope
            if exc.name != "flask":
                raise
            raise unittest.SkipTest("flask is not installed in this environment") from exc
        self.payload = build_decision_autopsy_payload

    def add_decision(self, at: str) -> int:
        return record_manager_decision_history(
            {"symbol": "MSFT", "decision_run_at": at, "manager_decision": "call",
             "manager_input_json": {"company": {"symbol": "MSFT"}}, "manager_output_json": {"recommendation": {}}},
            db_path=self.db_path)

    def add_outcome(self, decision_id: int, order_id: str, **values) -> None:
        columns = {"option_symbol": "MSFT1", "entry_order_id": order_id, "opened_at": "2026-03-02T15:00:00+00:00",
                   "manager_decision_history_id": decision_id, **values}
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute(f"INSERT INTO position_outcomes ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                         tuple(columns.values()))

    def records(self) -> dict[int, dict]:
        return {item["manager_decision_history_id"]: item for item in self.payload(limit=10)["records"]}

    def test_a_traded_decision_carries_its_returns_against_the_market(self) -> None:
        decision_id = self.add_decision("2026-03-02T15:00:00+00:00")
        self.add_outcome(decision_id, "o1", closed_at="2026-03-05T15:00:00+00:00", underlying_move=3.0,
                         spy_return=1.0, sector_return=2.0, residual_move=1.0, iv_at_entry=0.4, iv_at_exit=0.35,
                         return_1d_after_exit=-0.5, return_3d_after_exit=0.8)
        self.assertEqual(self.records()[decision_id]["benchmark"], {
            "underlying_move": 3.0, "spy_return": 1.0, "sector_return": 2.0, "residual_move": 1.0,
            "iv_at_entry": 0.4, "iv_at_exit": 0.35, "return_1d_after_exit": -0.5, "return_3d_after_exit": 0.8})

    def test_a_decision_without_an_outcome_has_no_benchmark(self) -> None:
        decision_id = self.add_decision("2026-03-02T15:00:00+00:00")
        self.assertIsNone(self.records()[decision_id]["benchmark"])


if __name__ == "__main__":
    unittest.main()
