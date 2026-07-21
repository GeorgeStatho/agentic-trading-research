from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from db_common import get_connection  # noqa: E402
from market_db import (  # noqa: E402
    initialize_database,
    list_decision_expectancy_rows,
    record_manager_decision_closed_outcome,
)

try:
    from flask import Flask
    from api_routes.dashboard import register_dashboard_routes
    from api_support.dashboard import (
        _normalize_selector_mode,
        build_decision_expectancy_payload,
    )

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    FLASK_AVAILABLE = False


def _row(
    *,
    identifier: int,
    confidence: str = "high",
    dte_bucket: str = "20_30",
    selector: str = "deterministic_high_confidence_hybrid",
    sector: str | None = "technology",
    marked_pnl: float | None = None,
    expires_at: str | None = "2026-06-30T00:00:00+00:00",
    realized_pnl: float | None = None,
    outcome_status: str | None = "open",
    decision_time: str = "2026-06-14T14:20:00+00:00",
) -> dict:
    return {
        "id": identifier,
        "decision_run_at": decision_time,
        "manager_decision": "call",
        "manager_confidence": confidence,
        "target_dte_bucket": dte_bucket,
        "selected_option_source": selector,
        "trade_executed": 1,
        "latest_trade_pnl_pct": marked_pnl,
        "pnl_expires_at": expires_at,
        "realized_pnl_pct": realized_pnl,
        "outcome_status": outcome_status,
        "resolved_outcome_label": "closed_profit" if realized_pnl and realized_pnl > 0 else "open_loss",
        "symbol": "MSFT",
        "industry_key": "software",
        "sector_key": sector,
    }


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class DecisionExpectancyDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 6, 15, tzinfo=timezone.utc)

    def _payload(self, rows: list[dict], **kwargs) -> dict:
        with patch("api_support.dashboard.list_decision_expectancy_rows", return_value=rows):
            return build_decision_expectancy_payload(now=self.now, min_samples=2, **kwargs)

    def test_confidence_dte_sector_and_time_zone_cohorts_use_deterministic_values(self) -> None:
        rows = [
            _row(identifier=1, marked_pnl=10.0),
            _row(identifier=2, marked_pnl=-5.0),
            _row(identifier=3, confidence="medium", dte_bucket="30_45", selector="simple_selector", sector=None, marked_pnl=0.0),
        ]
        confidence_payload = self._payload(rows, group_by="confidence")
        high = next(item for item in confidence_payload["cohorts"] if item["key"] == "high")
        self.assertEqual(high["trade_count"], 2)
        self.assertEqual(high["pnl_sample_count"], 2)
        self.assertEqual(high["win_count"], 1)
        self.assertEqual(high["loss_count"], 1)
        self.assertEqual(high["avg_pnl_pct"], 2.5)
        self.assertEqual(high["median_pnl_pct"], 2.5)
        self.assertEqual(high["expectancy_pct"], 2.5)
        self.assertEqual(high["profit_factor_like"], 2.0)
        self.assertFalse(high["insufficient_sample"])

        dte_payload = self._payload(rows, group_by="confidence_x_dte_bucket")
        self.assertTrue(any(item["key"] == "high|20_30" for item in dte_payload["cohorts"]))
        sector_payload = self._payload(rows, group_by="sector")
        self.assertTrue(any(item["key"] == "unknown" for item in sector_payload["cohorts"]))
        time_payload = self._payload(rows, group_by="time_of_day")
        self.assertEqual(time_payload["cohorts"][0]["key"], "opening_hour")

    def test_selector_normalization_and_basis_eligibility_handle_pending_and_expired_outcomes(self) -> None:
        rows = [
            _row(identifier=1, selector="fallback_guardrail", marked_pnl=4.0),
            _row(identifier=2, marked_pnl=8.0, expires_at="2026-06-10T00:00:00+00:00"),
            _row(identifier=3, marked_pnl=None, realized_pnl=12.0, outcome_status="closed"),
            _row(identifier=4, marked_pnl=None, realized_pnl=3.0, outcome_status="exit_pending"),
        ]
        marked = self._payload(rows, group_by="selector_mode")
        fallback = next(item for item in marked["cohorts"] if item["key"] == "fallback_or_guardrail")
        self.assertEqual(fallback["pnl_sample_count"], 1)
        self.assertEqual(marked["summary"]["pnl_sample_count"], 1)
        self.assertEqual(marked["summary"]["unresolved_count"], 3)

        closed = self._payload(rows, group_by="confidence", basis="closed")
        cohort = closed["cohorts"][0]
        self.assertEqual(cohort["pnl_sample_count"], 1)
        self.assertEqual(cohort["avg_pnl_pct"], 12.0)
        self.assertEqual(closed["summary"]["unresolved_count"], 3)
        self.assertEqual(_normalize_selector_mode("greeks_filtered"), "greeks")
        self.assertEqual(_normalize_selector_mode("manager_only"), "manager_only")

    def test_invalid_api_parameters_return_bad_request(self) -> None:
        app = Flask(__name__)
        register_dashboard_routes(app)
        client = app.test_client()
        self.assertEqual(client.get("/api/decision-expectancy?group_by=bad").status_code, 400)
        self.assertEqual(client.get("/api/decision-expectancy?basis=future").status_code, 400)
        self.assertEqual(client.get("/api/decision-expectancy?within_days=9999").status_code, 400)
        self.assertEqual(client.get("/api/decision-expectancy?sector=not/a-sector").status_code, 400)


class DecisionExpectancyReaderTests(unittest.TestCase):
    def test_reader_preserves_symbol_when_company_hierarchy_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "expectancy-reader.db"
            initialize_database(db_path=db_path, schema_path=DATA_DIR / "market_schema.sql")
            with get_connection(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO manager_decision_history (
                        symbol, decision_run_at, manager_decision, manager_confidence,
                        trade_executed, latest_trade_pnl_pct, pnl_expires_at
                    ) VALUES ('ORPH', '2026-06-14T14:20:00+00:00', 'call', 'high', 1, 4.5, '2026-06-30T00:00:00+00:00')
                    """
                )
            rows = list_decision_expectancy_rows(
                within_days=90,
                as_of="2026-06-15T00:00:00+00:00",
                db_path=db_path,
            )
        self.assertEqual(rows[0]["symbol"], "ORPH")
        self.assertIsNone(rows[0]["industry_key"])
        self.assertIsNone(rows[0]["sector_key"])

    def test_closed_outcomes_require_explicit_broker_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "expectancy-closed.db"
            initialize_database(db_path=db_path, schema_path=DATA_DIR / "market_schema.sql")
            with get_connection(db_path) as conn:
                decision_id = conn.execute(
                    """
                    INSERT INTO manager_decision_history (symbol, decision_run_at, trade_executed)
                    VALUES ('MSFT', '2026-06-14T14:20:00+00:00', 1)
                    """
                ).lastrowid
            self.assertFalse(record_manager_decision_closed_outcome(
                decision_history_id=decision_id,
                realized_pnl_pct=8.0,
                closed_at="2026-06-15T14:20:00+00:00",
                db_path=db_path,
            ))
            self.assertTrue(record_manager_decision_closed_outcome(
                decision_history_id=decision_id,
                realized_pnl_pct=8.0,
                closed_at="2026-06-15T14:20:00+00:00",
                resolved_outcome_label="closed_profit",
                broker_confirmed=True,
                db_path=db_path,
            ))
            with get_connection(db_path) as conn:
                row = conn.execute(
                    "SELECT outcome_status, realized_pnl_pct, closed_at FROM manager_decision_history WHERE id = ?",
                    (decision_id,),
                ).fetchone()
        self.assertEqual(row["outcome_status"], "closed")
        self.assertEqual(row["realized_pnl_pct"], 8.0)
        self.assertEqual(row["closed_at"], "2026-06-15T14:20:00+00:00")


if __name__ == "__main__":
    unittest.main()
