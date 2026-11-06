"""Read-only report routes over data the bot already stores."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib
import unittest

from db_common import get_connection
from tests.db_fixtures import temporary_database


class ReportRouteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.app = importlib.import_module("api").app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                raise unittest.SkipTest("Flask is not installed in this environment.") from exc
            raise

    def setUp(self) -> None:
        self.db = temporary_database(self)
        self.client = self.app.test_client()

    def execute(self, sql: str, *rows) -> None:
        with closing(get_connection(self.db)) as conn, conn:
            conn.executemany(sql, rows)


class ExecutionQualityTests(ReportRouteTestCase):
    def test_empty_database_has_no_rows(self):
        response = self.client.get("/api/execution-quality")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["rows"], [])

    def test_fills_are_summarised_by_account_and_side(self):
        today = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        self.execute(
            """INSERT INTO option_trade_executions (order_id, option_symbol, paper, order_side, order_qty,
               filled_qty, submitted_at, fill_observed_at, fill_price, slippage_vs_mid, spread_paid_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("a", "AAPL1", 1, "buy", 1, 1, today, today, 2.0, 0.05, 4.0),
            ("b", "AAPL2", 1, "buy", 2, 0, today, None, None, None, None),
            ("c", "AAPL3", 1, "buy", 1, 1, old, old, 2.0, 0.5, 9.0),
        )
        payload = self.client.get("/api/execution-quality?days=30").get_json()
        self.assertEqual(payload["days"], 30)
        [row] = payload["rows"]
        self.assertEqual((row["paper"], row["order_side"], row["submitted_orders"]), (1, "buy", 2))
        self.assertAlmostEqual(row["full_fill_rate"], 0.5)
        self.assertAlmostEqual(row["mean_slippage_vs_mid"], 0.05)


if __name__ == "__main__":
    unittest.main()
