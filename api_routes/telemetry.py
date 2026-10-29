from __future__ import annotations

from flask import jsonify, request

from api_support.request_utils import internal_error
from api_support.telemetry import (
    build_telemetry_summary_payload,
    is_startup_receiver_request_authorized,
    is_summary_request_authorized,
    record_startup_ping,
    validate_startup_ping_payload,
)


def register_telemetry_routes(app) -> None:
    @app.post("/api/telemetry/startup")
    def telemetry_startup():
        if not is_startup_receiver_request_authorized(request.headers.get("Authorization")):
            return jsonify({"error": "Unauthorized telemetry receiver request."}), 401

        payload = request.get_json(silent=True)
        validation_error = validate_startup_ping_payload(payload)
        if validation_error is not None:
            return jsonify({"error": validation_error}), 400

        try:
            return jsonify(record_startup_ping(payload)), 200
        except Exception as exc:
            return internal_error("Failed to record startup telemetry.", exc)

    @app.get("/api/telemetry/summary")
    def telemetry_summary():
        if not is_summary_request_authorized(request.headers.get("Authorization")):
            return jsonify({"error": "Unauthorized telemetry receiver request."}), 401

        try:
            project = request.args.get("project")
            return jsonify(build_telemetry_summary_payload(project=project)), 200
        except Exception as exc:
            return internal_error("Failed to load telemetry summary.", exc)
