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


class MacroCalendarTests(ReportRouteTestCase):
    def add_event(self, key: str, at: datetime, importance: str = "high") -> None:
        self.execute(
            """INSERT INTO macro_events (source, event_key, event_name, event_date, country, currency,
               importance, forecast, previous, scheduled_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("calendar", key, f"Event {key}", at.date().isoformat(), "United States", "USD", importance,
             "1%", "0.5%", at.isoformat()),
        )

    def test_upcoming_events_inside_the_window_in_time_order(self):
        from api_support.reports import macro_calendar_payload

        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        self.add_event("past", now - timedelta(hours=1))
        self.add_event("later", now + timedelta(days=2))
        self.add_event("soon", now + timedelta(hours=3), importance="low")
        self.add_event("beyond", now + timedelta(days=9))
        payload = macro_calendar_payload(days=7, now=now)
        self.assertEqual([event["event_name"] for event in payload["events"]], ["Event soon", "Event later"])
        self.assertEqual(payload["events"][0]["importance"], "low")
        self.assertEqual(payload["calendar_through"], (now + timedelta(days=9)).isoformat())

    def test_route_answers_an_empty_calendar(self):
        payload = self.client.get("/api/macro-calendar?days=7").get_json()
        self.assertEqual((payload["days"], payload["events"], payload["calendar_through"]), (7, [], None))


class LlmUsageTests(ReportRouteTestCase):
    def test_spend_is_totalled_and_split_by_day_model_provider_and_stage(self):
        from api_support.reports import llm_usage_payload

        now = datetime(2026, 11, 6, 18, tzinfo=timezone.utc)
        self.execute(
            """INSERT INTO llm_call_usage (called_at, model, upstream_provider, prompt_tokens, completion_tokens,
               reasoning_tokens, cost_usd, stage) VALUES (?,?,?,?,?,?,?,?)""",
            ("2026-11-06T10:00:00+00:00", "deepseek", "Wafer", 100, 10, 5, 0.25, "manager"),
            ("2026-11-06T11:00:00+00:00", "deepseek", "Morph", 100, 10, 5, 0.50, "strategist"),
            ("2026-11-05T11:00:00+00:00", "deepseek", "Wafer", 100, 10, 5, 0.25, None),
            ("2026-09-01T11:00:00+00:00", "deepseek", "Wafer", 100, 10, 5, 9.00, "manager"),
        )
        payload = llm_usage_payload(days=30, now=now)
        self.assertEqual(payload["totals"], {"calls": 3, "cost_usd": 1.0, "prompt_tokens": 300,
                                             "completion_tokens": 30, "reasoning_tokens": 15})
        self.assertEqual(payload["last_7_days_cost_usd"], 1.0)
        self.assertEqual([(row["key"], row["calls"], row["cost_usd"]) for row in payload["by_stage"]],
                         [("strategist", 1, 0.5), ("manager", 1, 0.25), ("unknown", 1, 0.25)])
        self.assertEqual([(row["key"], row["cost_usd"]) for row in payload["by_day"]],
                         [("2026-11-06", 0.75), ("2026-11-05", 0.25)])
        self.assertEqual([row["key"] for row in payload["by_provider"]], ["Wafer", "Morph"])
        self.assertEqual([row["key"] for row in payload["by_model"]], ["deepseek"])

    def test_route_answers_an_empty_table(self):
        payload = self.client.get("/api/llm-usage?days=30").get_json()
        self.assertEqual((payload["days"], payload["totals"]["calls"], payload["by_stage"]), (30, 0, []))


if __name__ == "__main__":
    unittest.main()
