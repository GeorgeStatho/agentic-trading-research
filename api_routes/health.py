from __future__ import annotations

from flask import jsonify

from api_support.common import internal_error
from portfolio_history_service import fetch_portfolio_history


def register_health_routes(app) -> None:
    @app.get("/api/health")
    def health() -> tuple[dict, int]:
        return {"status": "ok"}, 200

    @app.get("/api/portfolio-history")
    def portfolio_history():
        try:
            payload = fetch_portfolio_history()
            return jsonify(payload), 200
        except Exception as exc:
            return internal_error("Failed to load portfolio history.", exc)
