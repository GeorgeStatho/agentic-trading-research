from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from db_common import use_database
from market_db_modules.bootstrap import initialize_database

try:
    from api_support.decision_autopsy import todays_decision_funnel
    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    FLASK_AVAILABLE = False


def _decide(conn, history_id: int, run_at: str, strategist: str, manager: str, executed: bool) -> None:
    conn.execute(
        "INSERT INTO manager_decision_history (id, symbol, decision_run_at, strategist_decision, manager_decision, trade_executed, trade_execution_order_id)"
        " VALUES (?, 'ORCL', ?, ?, ?, ?, ?)",
        (history_id, run_at, strategist, manager, 1 if executed else 0, f"order-{history_id}" if executed else None),
    )
    conn.execute("INSERT INTO decision_autopsy_ledger (manager_decision_history_id, symbol, decision_run_at, snapshot_json) VALUES (?, 'ORCL', ?, '{}')",
                 (history_id, run_at))


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class TodaysDecisionFunnelTests(unittest.TestCase):
    def funnel(self, decisions, now):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "funnel.sqlite3"
            initialize_database(db_path=db_path)
            conn = sqlite3.connect(db_path)
            try:
                for index, decision in enumerate(decisions, start=1):
                    _decide(conn, index, *decision)
                conn.commit()
            finally:
                conn.close()
            with use_database(db_path):
                return todays_decision_funnel(now=now)

    def test_counts_each_stage_of_the_new_york_trading_day(self):
        now = datetime(2026, 11, 4, 21, 30, tzinfo=timezone.utc)  # 4:30 PM in New York
        funnel = self.funnel([
            ("2026-11-04T14:00:00+00:00", "trade_candidate", "call", True),
            ("2026-11-04T15:00:00+00:00", "trade_candidate", "put", False),
            ("2026-11-04T16:00:00+00:00", "trade_candidate", "neither", False),
            ("2026-11-04T17:00:00+00:00", "watchlist", "neither", False),
            # 11 PM New York on Nov 3: the previous trading day, though already Nov 4 in UTC.
            ("2026-11-04T04:00:00+00:00", "trade_candidate", "call", True),
        ], now)

        self.assertEqual(funnel, {"market_day": "2026-11-04", "evaluated": 4, "trade_candidates": 3, "call": 1, "put": 1, "executed": 1})

    def test_counts_every_decision_not_just_the_newest_hundred(self):
        now = datetime(2026, 11, 4, 21, 30, tzinfo=timezone.utc)
        decisions = [(f"2026-11-04T{14 + index // 60:02d}:{index % 60:02d}:00+00:00", "watchlist", "neither", False) for index in range(130)]

        self.assertEqual(self.funnel(decisions, now)["evaluated"], 130)


if __name__ == "__main__":
    unittest.main()
