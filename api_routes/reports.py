from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import jsonify, request

from api_support.request_utils import internal_error, safe_int


def register_report_routes(app) -> None:
    """Read-only views over reports the bot already computes for its CLI tools."""

    @app.get("/api/execution-quality")
    def execution_quality():
        from market_db_modules.execution_telemetry import execution_telemetry_report

        days = safe_int(request.args.get("days"), 30, minimum=1, maximum=365)
        start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        try:
            return jsonify({"days": days, "rows": execution_telemetry_report(start=start)}), 200
        except Exception as exc:
            return internal_error("Failed to load execution quality.", exc)

    @app.get("/api/exit-attribution")
    def exit_attribution():
        from market_db_modules.position_outcomes import exit_attribution_report

        try:
            return jsonify({"rows": exit_attribution_report()}), 200
        except Exception as exc:
            return internal_error("Failed to load exit attribution.", exc)

    @app.get("/api/decision-attribution")
    def decision_attribution():
        from api_support.reports import ATTRIBUTION_HORIZONS, decision_attribution_payload

        days = safe_int(request.args.get("days"), 90, minimum=1, maximum=730)
        horizon = safe_int(request.args.get("horizon"), 5)
        if horizon not in ATTRIBUTION_HORIZONS:
            return jsonify({"error": f"horizon must be one of {ATTRIBUTION_HORIZONS}."}), 400
        try:
            return jsonify(decision_attribution_payload(days=days, horizon=horizon)), 200
        except Exception as exc:
            return internal_error("Failed to load decision attribution.", exc)

    @app.get("/api/macro-calendar")
    def macro_calendar():
        from api_support.reports import macro_calendar_payload

        try:
            return jsonify(macro_calendar_payload(days=safe_int(request.args.get("days"), 7, maximum=31))), 200
        except Exception as exc:
            return internal_error("Failed to load the macro calendar.", exc)

    @app.get("/api/llm-usage")
    def llm_usage():
        from api_support.reports import llm_usage_payload

        try:
            return jsonify(llm_usage_payload(days=safe_int(request.args.get("days"), 30, maximum=365))), 200
        except Exception as exc:
            return internal_error("Failed to load LLM usage.", exc)
