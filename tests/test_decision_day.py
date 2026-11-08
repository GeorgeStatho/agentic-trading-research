"""The Decisions page reads one New York trading day at a time."""
from __future__ import annotations

import csv
import io
import unittest

from market_db_modules.history_records import record_manager_decision_history
from tests.db_fixtures import temporary_database
from tests.test_csv_export import ApiTestCase


class DecisionDayRouteTests(ApiTestCase):
    def setUp(self) -> None:
        self.db = temporary_database(self)
        self.client = self.app.test_client()
        for symbol, run_at, decision in (
            ("EARLY", "2026-11-04T04:59:00+00:00", "call"),  # 11:59 PM New York, Nov 3
            ("FIRST", "2026-11-04T05:01:00+00:00", "call"),  # 12:01 AM New York, Nov 4
            ("LAST", "2026-11-05T04:59:00+00:00", "put"),    # 11:59 PM New York, Nov 4
            ("NEXT", "2026-11-05T05:01:00+00:00", "call"),   # 12:01 AM New York, Nov 5
        ):
            record_manager_decision_history(
                {"symbol": symbol, "decision_run_at": run_at, "manager_decision": decision,
                 "manager_input_json": {"company": {"symbol": symbol}}, "manager_output_json": {"recommendation": {}}},
                db_path=self.db)

    def test_a_day_returns_only_that_new_york_day_with_its_funnel(self):
        payload = self.client.get("/api/decision-autopsies?day=2026-11-04").get_json()
        self.assertEqual([record["symbol"] for record in payload["records"]], ["LAST", "FIRST"])
        self.assertEqual(payload["day"], {"market_day": "2026-11-04", "evaluated": 2, "trade_candidates": 0,
                                          "call": 1, "put": 1, "executed": 0})

    def test_without_a_day_the_newest_decisions_load_and_there_is_no_day_funnel(self):
        payload = self.client.get("/api/decision-autopsies").get_json()
        self.assertEqual(payload["count"], 4)
        self.assertNotIn("day", payload)

    def test_the_csv_export_honours_the_day(self):
        response = self.client.get("/api/decision-autopsies?day=2026-11-05&format=csv")
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual([row[rows[0].index("symbol")] for row in rows[1:]], ["NEXT"])

    def test_a_malformed_day_is_rejected(self):
        self.assertEqual(self.client.get("/api/decision-autopsies?day=yesterday").status_code, 400)


if __name__ == "__main__":
    unittest.main()
