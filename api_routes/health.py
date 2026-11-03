from __future__ import annotations

from flask import jsonify

from api_support.dashboard import read_bot_components, summarize_bot_status
from api_support.request_utils import internal_error
from portfolio_history_service import fetch_portfolio_history


def register_health_routes(app) -> None:
    @app.get("/api/health")
    def health() -> tuple[dict, int]:
        return {"status": "ok"}, 200

    @app.get("/api/health/components")
    def component_health():
        # For uptime monitors: 503 when any loop is down, stalled, errored or failed recently.
        summary = summarize_bot_status(read_bot_components())
        return jsonify(summary), 200 if summary["state"] == "healthy" else 503

    @app.get("/api/portfolio-history")
    def portfolio_history():
        try:
            payload = fetch_portfolio_history()
            return jsonify(payload), 200
        except Exception as exc:
            return internal_error("Failed to load portfolio history.", exc)
