from __future__ import annotations

from flask import jsonify, request

from api_support.common import internal_error, safe_int
from api_support.decision_autopsy import build_decision_autopsy_payload


def register_decision_autopsy_routes(app) -> None:
    @app.get("/api/decision-autopsies")
    def decision_autopsies():
        try:
            return jsonify(build_decision_autopsy_payload(limit=safe_int(request.args.get("limit"), 100, maximum=250))), 200
        except Exception as exc:
            return internal_error("Failed to load the decision autopsy ledger.", exc)
