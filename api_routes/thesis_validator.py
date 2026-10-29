from __future__ import annotations

from flask import jsonify, request

from api_support.request_utils import internal_error, safe_int
from api_support.thesis_validator import (
    DEFAULT_HEALTH_WINDOW_DAYS,
    DEFAULT_RUN_LIMIT,
    build_thesis_validator_payload,
)


def register_thesis_validator_routes(app) -> None:
    @app.get("/api/thesis-validator")
    def thesis_validator():
        try:
            payload = build_thesis_validator_payload(
                limit=safe_int(request.args.get("limit"), DEFAULT_RUN_LIMIT, minimum=1, maximum=500),
                health_window_days=safe_int(
                    request.args.get("days"), DEFAULT_HEALTH_WINDOW_DAYS, minimum=1, maximum=365
                ),
            )
            return jsonify(payload), 200
        except Exception as exc:
            return internal_error("Failed to load thesis validator runs.", exc)
