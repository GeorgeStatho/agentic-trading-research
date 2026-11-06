"""The trade, decision and expectancy tables download as CSV."""
from __future__ import annotations

from contextlib import closing
import csv
from datetime import datetime, timezone
import importlib
import io
import unittest
from unittest import mock

from db_common import get_connection
from market_db_modules.history_records import record_manager_decision_history
from tests.db_fixtures import temporary_database
from tests.test_dashboard_decision_expectancy import _row


def _rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.get_data(as_text=True))))


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.app = importlib.import_module("api").app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                raise unittest.SkipTest("Flask is not installed in this environment.") from exc
            raise


class CsvResponseTests(ApiTestCase):
    def test_values_are_quoted_and_nested_paths_and_gaps_are_read(self):
        from api_support.request_utils import csv_response

        with self.app.test_request_context():
            response = csv_response([{"name": 'a, "b"', "outcome": {"pnl": 1.5}}, {"name": "c", "outcome": None}],
                                    ("name", "outcome.pnl"), "trades.csv")
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn('filename=trades.csv', response.headers["Content-Disposition"])
        self.assertEqual(_rows(response), [["name", "outcome.pnl"], ['a, "b"', "1.5"], ["c", ""]])

    def test_text_a_spreadsheet_would_run_as_a_formula_is_neutralised(self):
        from api_support.request_utils import csv_response

        with self.app.test_request_context():
            response = csv_response([{"name": "=HYPERLINK(1)", "pnl": -5.0}], ("name", "pnl"), "x.csv")
        self.assertEqual(_rows(response)[1], ["'=HYPERLINK(1)", "-5.0"])


class CsvRouteTests(ApiTestCase):
    def setUp(self) -> None:
        self.db = temporary_database(self)
        self.client = self.app.test_client()

    def test_executed_trades(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("""INSERT INTO option_trade_executions (order_id, option_symbol, underlying_symbol, paper,
                order_side, filled_qty, fill_price, submitted_at) VALUES ('o1', 'MSFT1', 'MSFT', 1, 'buy', 1, 2.5,
                '2026-11-05T15:00:00+00:00')""")
        response = self.client.get("/api/executed-trades?format=csv")
        rows = _rows(response)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn("option_symbol", rows[0])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][rows[0].index("fill_price")], "2.5")

    def test_decision_autopsies(self):
        record_manager_decision_history(
            {"symbol": "MSFT", "decision_run_at": "2026-11-05T15:00:00+00:00", "manager_decision": "call",
             "manager_input_json": {"company": {"symbol": "MSFT"}}, "manager_output_json": {"recommendation": {}}},
            db_path=self.db)
        rows = _rows(self.client.get("/api/decision-autopsies?format=csv"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][rows[0].index("symbol")], "MSFT")
        self.assertIn("manager.decision", rows[0])

    def test_decision_expectancy_keeps_its_filters(self):
        rows = [_row(identifier=1, marked_pnl=10.0), _row(identifier=2, marked_pnl=-5.0, confidence="medium")]
        with mock.patch("api_support.decision_expectancy.list_decision_expectancy_rows", return_value=rows):
            response = self.client.get("/api/decision-expectancy?group_by=confidence&min_samples=1&format=csv")
        table = _rows(response)
        self.assertEqual(table[0][:2], ["key", "label"])
        self.assertEqual(sorted(row[0] for row in table[1:]), ["high", "medium"])

    def test_json_stays_the_default(self):
        self.assertEqual(self.client.get("/api/executed-trades").mimetype, "application/json")


if __name__ == "__main__":
    unittest.main()
