from __future__ import annotations

from flask import jsonify, request

from api_support.common import internal_error, safe_int
from api_support.v2_status import (
    DEFAULT_ACTIVITY_WINDOW_DAYS,
    DEFAULT_RUN_LIMIT,
    build_v2_status_payload,
)


def register_v2_status_routes(app) -> None:
    @app.get("/api/v2-status")
    def v2_status():
        try:
            payload = build_v2_status_payload(
                window_days=safe_int(
                    request.args.get("days"), DEFAULT_ACTIVITY_WINDOW_DAYS, minimum=1, maximum=365
                ),
                run_limit=safe_int(request.args.get("runs"), DEFAULT_RUN_LIMIT, minimum=1, maximum=25),
            )
            return jsonify(payload), 200
        except Exception as exc:
            return internal_error("Failed to load the V2 rollout status.", exc)
