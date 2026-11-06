from __future__ import annotations

from flask import jsonify, request

from api_support.request_utils import csv_response, internal_error, safe_int
from api_support.decision_autopsy import build_decision_autopsy_payload

# The flat facts of each decision; evidence, constraints and model prose stay in the JSON.
DECISION_CSV_COLUMNS = (
    "decision_run_at", "symbol", "company_name", "strategist.decision", "strategist.confidence", "manager.decision",
    "manager.confidence", "constraints.selected_option_symbol", "execution.status", "outcome.trade_executed",
    "outcome.pnl_pct", "outcome.realized_pnl_pct", "outcome.exit_reason", "outcome.closed_at",
    "attribution.evidence_quality", "attribution.direction_outcome", "attribution.primary_failure",
    "benchmark.underlying_move", "benchmark.spy_return", "benchmark.sector_return", "benchmark.residual_move",
)


def register_decision_autopsy_routes(app) -> None:
    @app.get("/api/decision-autopsies")
    def decision_autopsies():
        try:
            payload = build_decision_autopsy_payload(limit=safe_int(request.args.get("limit"), 100, maximum=250))
            if request.args.get("format") == "csv":
                return csv_response(payload["records"], DECISION_CSV_COLUMNS, "decisions.csv")
            return jsonify(payload), 200
        except Exception as exc:
            return internal_error("Failed to load the decision autopsy ledger.", exc)
