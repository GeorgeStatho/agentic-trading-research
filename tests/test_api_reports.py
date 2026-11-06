"""Read-only report routes over data the bot already stores."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib
import unittest
from unittest import mock

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


class AttributionTests(ReportRouteTestCase):
    def test_exit_attribution_reports_profit_given_back_per_exit_reason(self):
        self.execute(
            """INSERT INTO position_outcomes (option_symbol, entry_order_id, opened_at, closed_at,
               primary_exit_reason, mfe_option, realized_pnl_pct) VALUES (?,?,?,?,?,?,?)""",
            ("A1", "o1", "2026-11-01", "2026-11-02", "stop_loss", 10.0, -30.0),
            ("A2", "o2", "2026-11-01", "2026-11-03", "stop_loss", 20.0, -30.0),
            ("A3", "o3", "2026-11-01", None, "stop_loss", 50.0, None),
        )
        [row] = self.client.get("/api/exit-attribution").get_json()["rows"]
        self.assertEqual((row["exit_reason"], row["closed_positions"]), ("stop_loss", 2))
        self.assertAlmostEqual(row["median_mfe_minus_realized_pct"], 45.0)

    def test_decision_attribution_answers_an_empty_database(self):
        payload = self.client.get("/api/decision-attribution?days=90&horizon=10").get_json()
        self.assertEqual((payload["days"], payload["horizon"], payload["total"]), (90, 10, 0))

    def test_decision_attribution_groups_become_lists_ordered_by_size(self):
        from api_support.reports import decision_attribution_payload

        group = {"count": 3, "graded": 2, "hit_rate": 0.5, "hit_rate_se": 0.35}
        report = {"days": 30, "horizon": 5, "total": 4,
                  "matrix": {"weak": {**group, "count": 1, "cells": {}}, "strong": {**group, "cells": {}}},
                  "by_evidence_source": {"proxy": group}, "model_self_rating_calibration": {"high": group},
                  "primary_failure_counts": {"none": 1, "thin": 3}}
        with mock.patch("api_support.reports.decision_attribution_report", return_value=report) as build:
            payload = decision_attribution_payload(days=30, horizon=5)
        build.assert_called_once_with(days=30, horizon=5)
        self.assertEqual([row["key"] for row in payload["by_quality"]], ["strong", "weak"])
        self.assertEqual(payload["by_source"], [{"key": "proxy", **group}])
        self.assertEqual(payload["primary_failures"], [{"key": "thin", "count": 3}, {"key": "none", "count": 1}])

    def test_decision_attribution_refuses_an_unknown_horizon(self):
        self.assertEqual(self.client.get("/api/decision-attribution?horizon=7").status_code, 400)


if __name__ == "__main__":
    unittest.main()
