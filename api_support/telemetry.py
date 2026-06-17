from __future__ import annotations

import os
from typing import Any

from db_helpers import DB_PATH, get_telemetry_summary, initialize_market_database, record_telemetry_installation


def _is_bearer_token_authorized(
    authorization_header: str | None,
    expected_token: str,
) -> bool:
    if not expected_token:
        return True

    header_value = str(authorization_header or "").strip()
    if not header_value.startswith("Bearer "):
        return False
    received_token = header_value.removeprefix("Bearer ").strip()
    return bool(received_token) and received_token == expected_token


def is_startup_receiver_request_authorized(authorization_header: str | None) -> bool:
    expected_token = str(os.getenv("TELEMETRY_RECEIVER_WRITE_AUTH_TOKEN", "")).strip()
    return _is_bearer_token_authorized(authorization_header, expected_token)


def is_summary_request_authorized(authorization_header: str | None) -> bool:
    expected_token = str(os.getenv("TELEMETRY_RECEIVER_READ_AUTH_TOKEN", "")).strip()
    if not expected_token:
        expected_token = str(os.getenv("TELEMETRY_RECEIVER_AUTH_TOKEN", "")).strip()
    return _is_bearer_token_authorized(authorization_header, expected_token)


def validate_startup_ping_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "Telemetry payload must be a JSON object."
    if str(payload.get("event") or "").strip() != "startup":
        return "Telemetry payload must include event='startup'."
    if not str(payload.get("install_id") or "").strip():
        return "Telemetry payload must include install_id."
    if not str(payload.get("runtime") or "").strip():
        return "Telemetry payload must include runtime."
    return None


def record_startup_ping(payload: dict[str, Any]) -> dict[str, Any]:
    initialize_market_database(db_path=DB_PATH)
    record = record_telemetry_installation(payload, db_path=DB_PATH)
    if record is None:
        raise ValueError("Telemetry payload could not be recorded.")
    return {
        "status": "ok",
        "install_id": str(record.get("install_id") or "").strip(),
        "startup_ping_count": int(record.get("startup_ping_count") or 0),
        "first_seen_at": str(record.get("first_seen_at") or "").strip(),
        "last_seen_at": str(record.get("last_seen_at") or "").strip(),
        "last_runtime": str(record.get("last_runtime") or "").strip(),
    }


def build_telemetry_summary_payload(*, project: str | None = None) -> dict[str, Any]:
    initialize_market_database(db_path=DB_PATH)
    payload = get_telemetry_summary(project=project, db_path=DB_PATH)
    payload["status"] = "ok"
    return payload
