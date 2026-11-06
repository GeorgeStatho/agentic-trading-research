"""The shape of API payloads the dashboard reads, pinned in web_dashboard/src/test/contracts/.

Each contract is the sorted list of key paths in a populated payload (`a.b`, `items[].c`). When a
payload change is intended, regenerate the contracts and update the dashboard's types and fixtures:
    UPDATE_API_CONTRACTS=1 python3 -m pytest tests/test_api_contracts.py
The dashboard's contracts.test.ts fails while a typed fixture still uses a path the API dropped.
"""
from datetime import datetime, timezone
import json
import os
import unittest
from unittest import mock

from tests import ROOT_DIR
from tests.db_fixtures import temporary_database
from tests.test_dashboard_decision_expectancy import _row


CONTRACTS_DIR = ROOT_DIR / "web_dashboard" / "src" / "test" / "contracts"


def key_paths(value, prefix: str = "") -> set[str]:
    paths = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths |= key_paths(child, path)
    elif isinstance(value, list):
        for item in value:
            paths |= key_paths(item, f"{prefix}[]")
    return paths


def _expectancy_payload() -> dict:
    from api_support.decision_expectancy import build_decision_expectancy_payload

    rows = [_row(identifier=1, marked_pnl=12.0), _row(identifier=2, marked_pnl=-8.0, confidence="medium"),
            _row(identifier=3, realized_pnl=20.0, outcome_status="closed", confidence="medium")]
    with mock.patch("api_support.decision_expectancy.list_decision_expectancy_rows", return_value=rows):
        return build_decision_expectancy_payload(now=datetime(2026, 6, 15, tzinfo=timezone.utc), min_samples=1)


def _fake_alpaca(path, query=None):
    if path == "/v2/account":
        return {"equity": "2379.77", "last_equity": "2604.79", "buying_power": "2379.77", "cash": "2379.77",
                "portfolio_value": "2379.77", "status": "ACTIVE"}
    if path == "/v2/clock":
        return {"is_open": False, "timestamp": "2026-11-06T22:00:00Z", "next_open": "2026-11-09T14:30:00Z",
                "next_close": "2026-11-09T21:00:00Z"}
    if path == "/v2/positions":
        return [{"symbol": "AAPL261218C00200000", "asset_class": "us_option", "qty": "1", "market_value": "320",
                 "unrealized_pl": "-40", "unrealized_plpc": "-0.11", "avg_entry_price": "3.6"}]
    if path.startswith("/v2/account/activities"):
        return [{"symbol": "AAPL261218C00200000", "side": "buy", "qty": "1", "price": "3.6",
                 "transaction_time": "2026-11-02T15:00:00Z"},
                {"symbol": "AAPL261218C00200000", "side": "sell", "qty": "1", "price": "2.7",
                 "transaction_time": "2026-11-03T15:00:00Z"}]
    return {}


def _kpi_payload(test: unittest.TestCase) -> dict:
    from api_support.dashboard import build_dashboard_kpis

    history = {"timestamp": [1762300800, 1762387200, 1762473600], "equity": [2740.0, 2604.79, 2379.77],
               "profit_loss": [0.0, -135.21, -225.02], "profit_loss_pct": [0.0, -0.0493, -0.0864],
               "base_value": 2740.0, "timeframe": "1D"}
    rankings = {"top_sectors": [{"sector_key": "technology", "score": 74}],
                "top_industries_by_sector": {"technology": [{"industry_key": "semiconductors", "score": 35}]}}
    temporary_database(test)
    with mock.patch("api_support.dashboard.alpaca_get_json", side_effect=_fake_alpaca), \
            mock.patch("api_support.dashboard.fetch_portfolio_history", return_value=history), \
            mock.patch("api_support.dashboard.get_current_rankings", return_value=rankings):
        return build_dashboard_kpis()


def _execution_quality_payload(test: unittest.TestCase) -> dict:
    from api import app
    from db_common import get_connection
    from contextlib import closing

    temporary_database(test)
    now = datetime.now(timezone.utc).isoformat()
    with closing(get_connection()) as conn, conn:
        conn.execute("""INSERT INTO option_trade_executions (order_id, option_symbol, paper, order_side, order_qty,
            filled_qty, submitted_at, fill_observed_at, fill_price, slippage_vs_mid, slippage_vs_arrival, spread_paid_pct)
            VALUES ('a', 'AAPL1', 1, 'buy', 1, 1, ?, ?, 2.0, 0.05, 0.01, 4.0)""", (now, now))
    return app.test_client().get("/api/execution-quality?days=30").get_json()


def _exit_attribution_payload(test: unittest.TestCase) -> dict:
    from api import app
    from db_common import get_connection
    from contextlib import closing

    temporary_database(test)
    with closing(get_connection()) as conn, conn:
        conn.execute("""INSERT INTO position_outcomes (option_symbol, entry_order_id, opened_at, closed_at,
            primary_exit_reason, mfe_option, realized_pnl_pct) VALUES ('A1', 'o1', '2026-11-01', '2026-11-02',
            'stop_loss', 10.0, -30.0)""")
    return app.test_client().get("/api/exit-attribution").get_json()


def _decision_attribution_payload() -> dict:
    """The report's shape with every group present, as the live database has it."""
    from api_support.reports import decision_attribution_payload

    group = {"count": 2, "graded": 2, "hit_rate": 0.5, "hit_rate_se": 0.35}
    report = {"days": 90, "horizon": 5, "total": 2,
              "matrix": {"adequate": {**group, "cells": {"correct": {"count": 1, "mean_signed_return": 0.04}}}},
              "by_evidence_source": {"grounding_proxy": group}, "model_self_rating_calibration": {"high": group},
              "primary_failure_counts": {"none": 2}, "expression": {"captured": 1},
              "abstentions": {"neither": {"count": 1, "mean_abs_move": 0.02, "median_abs_move": 0.02}}}
    with mock.patch("api_support.reports.decision_attribution_report", return_value=report):
        return decision_attribution_payload(days=90, horizon=5)


def _macro_calendar_payload(test: unittest.TestCase) -> dict:
    from api_support.reports import macro_calendar_payload
    from db_common import get_connection
    from contextlib import closing

    temporary_database(test)
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    with closing(get_connection()) as conn, conn:
        conn.execute("""INSERT INTO macro_events (source, event_key, event_name, event_date, country, currency,
            importance, forecast, previous, scheduled_at_utc) VALUES ('calendar', 'k', 'ISM Services PMI',
            '2026-11-03', 'United States', 'USD', 'high', '52', '51', '2026-11-03T15:00:00+00:00')""")
    return macro_calendar_payload(days=7, now=now)


def _llm_usage_payload(test: unittest.TestCase) -> dict:
    from api_support.reports import llm_usage_payload
    from market_db_modules.llm_usage_store import record_llm_call_usage

    temporary_database(test)
    record_llm_call_usage({"model": "deepseek", "upstream_provider": "Wafer", "prompt_tokens": 10,
                           "completion_tokens": 2, "reasoning_tokens": 1, "cost_usd": 0.01, "stage": "manager"})
    return llm_usage_payload(days=30)


class ApiContractTests(unittest.TestCase):
    def assert_contract(self, name: str, payload: dict) -> None:
        paths = sorted(key_paths(payload))
        path = CONTRACTS_DIR / f"{name}.json"
        if os.environ.get("UPDATE_API_CONTRACTS"):
            CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(paths, indent=1) + "\n", encoding="utf-8")
        self.assertTrue(path.exists(), f"missing {path.relative_to(ROOT_DIR)}; run with UPDATE_API_CONTRACTS=1")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), paths)

    def test_decision_expectancy(self):
        self.assert_contract("decision-expectancy", _expectancy_payload())

    def test_dashboard_kpis(self):
        self.assert_contract("dashboard-kpis", _kpi_payload(self))

    def test_exit_attribution(self):
        self.assert_contract("exit-attribution", _exit_attribution_payload(self))

    def test_decision_attribution(self):
        self.assert_contract("decision-attribution", _decision_attribution_payload())

    def test_macro_calendar(self):
        self.assert_contract("macro-calendar", _macro_calendar_payload(self))

    def test_llm_usage(self):
        self.assert_contract("llm-usage", _llm_usage_payload(self))

    def test_execution_quality(self):
        self.assert_contract("execution-quality", _execution_quality_payload(self))


if __name__ == "__main__":
    unittest.main()
