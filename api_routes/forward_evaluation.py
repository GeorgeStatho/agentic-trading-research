from __future__ import annotations

from flask import jsonify, request

from api_support.request_utils import internal_error, safe_int
from api_support.forward_evaluation import DEFAULT_WINDOW_DAYS, build_forward_evaluation_payload


def register_forward_evaluation_routes(app) -> None:
    @app.get("/api/forward-evaluation")
    def forward_evaluation():
        try:
            payload = build_forward_evaluation_payload(
                window_days=safe_int(request.args.get("days"), DEFAULT_WINDOW_DAYS, minimum=1, maximum=365),
            )
            return jsonify(payload), 200
        except Exception as exc:
            return internal_error("Failed to load forward evaluation.", exc)
