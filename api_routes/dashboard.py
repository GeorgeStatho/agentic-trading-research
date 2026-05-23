from __future__ import annotations

from flask import jsonify

from api_support.common import internal_error, read_json_file
from api_support.context import SCRIPT_STATUS_PATH
from api_support.dashboard import (
    build_company_decisions_payload,
    build_dashboard_kpis,
    build_open_positions_payload,
    build_risk_controls_payload,
)


def register_dashboard_routes(app) -> None:
    @app.get("/api/script-status")
    def script_status():
        return read_json_file(SCRIPT_STATUS_PATH)

    @app.get("/api/dashboard-kpis")
    def dashboard_kpis():
        try:
            return jsonify(build_dashboard_kpis()), 200
        except Exception as exc:
            return internal_error("Failed to load dashboard KPIs.", exc)

    @app.get("/api/open-positions")
    def open_positions():
        try:
            return jsonify(build_open_positions_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load open positions.", exc)

    @app.get("/api/risk-controls")
    def risk_controls():
        try:
            return jsonify(build_risk_controls_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load risk controls.", exc)

    @app.get("/api/company-decisions")
    def company_decisions():
        try:
            return jsonify(build_company_decisions_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load company decisions.", exc)
