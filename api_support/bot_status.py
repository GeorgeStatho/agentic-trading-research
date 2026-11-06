"""Health of each long-running loop, read from the status files they heartbeat into."""
from __future__ import annotations

from datetime import datetime, timezone
from api_support.request_utils import read_json_payload
from api_support.context import (
    BOT_DOWN_THRESHOLD_SECONDS,
    BOT_ERROR_WINDOW_SECONDS,
    BOT_STALL_SECONDS,
    DB_BACKUP_STATUS_PATH,
    MARKETDATA_EOD_SYNC_STATUS_PATH,
    NEWS_COLLECTOR_STATUS_PATH,
    OPTION_MANAGER_STATUS_PATH,
    SCRIPT_STATUS_PATH,
)
from services.core.time_utils import parse_timestamp


def summarize_market_status(clock_payload: dict) -> dict:
    is_open = bool(clock_payload.get("is_open", False))
    next_open = str(clock_payload.get("next_open") or "")
    next_close = str(clock_payload.get("next_close") or "")
    timestamp = str(clock_payload.get("timestamp") or "")
    return {
        "state": "open" if is_open else "closed",
        "label": "Open" if is_open else "Closed",
        "detail": f"Next close: {next_close}" if is_open and next_close else f"Next open: {next_open}" if next_open else "",
        "timestamp": timestamp,
        "next_open": next_open,
        "next_close": next_close,
    }


def _age_seconds(value) -> float | None:
    parsed = parse_timestamp(str(value or ""))
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def status_snapshot(name: str, payload) -> dict:
    if not isinstance(payload, dict):
        return {"name": name, "state": "down", "label": "Down", "updated_at": ""}

    updated_at = str(payload.get("updated_at") or "")
    age = _age_seconds(updated_at)
    raw_state = str(payload.get("state") or "").strip().lower()
    state = raw_state if raw_state in {"starting", "running", "paused", "error"} else "unknown"
    # Writers heartbeat while alive, so staleness means the process is gone.
    if age is None or age > BOT_DOWN_THRESHOLD_SECONDS:
        state = "down"
    elif state == "running" and (_age_seconds(payload.get("state_since")) or 0) > BOT_STALL_SECONDS:
        state = "stalled"

    error_age = _age_seconds(payload.get("last_error_at"))
    label = state.replace("_", " ").title()
    return {
        "name": name,
        "state": state,
        "label": label,
        "updated_at": updated_at,
        "state_since": str(payload.get("state_since") or ""),
        "message": str(payload.get("message") or ""),
        "last_error": str(payload.get("last_error") or ""),
        "last_error_at": str(payload.get("last_error_at") or ""),
        "recent_error": error_age is not None and error_age <= BOT_ERROR_WINDOW_SECONDS,
    }


def is_component_healthy(component: dict) -> bool:
    return component["state"] in {"running", "paused", "starting"} and not component.get("recent_error")


def read_bot_components() -> list[tuple[str, object]]:
    return [
        ("Worker", read_json_payload(SCRIPT_STATUS_PATH)),
        ("Option Manager", read_json_payload(OPTION_MANAGER_STATUS_PATH)),
        ("News Collector", read_json_payload(NEWS_COLLECTOR_STATUS_PATH)),
        ("EOD Sync", read_json_payload(MARKETDATA_EOD_SYNC_STATUS_PATH)),
        ("DB Backup", read_json_payload(DB_BACKUP_STATUS_PATH)),
    ]


def summarize_bot_status(statuses: list[tuple[str, object]]) -> dict:
    snapshots = [status_snapshot(name, payload) for name, payload in statuses]
    # A component that has never written a status is not deployed here, so it is left out.
    components = [
        snapshot for snapshot in snapshots
        if snapshot.get("updated_at") or snapshot.get("state") != "down"
    ] or snapshots[:1]

    if all(is_component_healthy(component) for component in components):
        overall_state = "healthy"
        label = "Healthy"
    elif any(component["state"] in {"running", "paused", "starting"} for component in components):
        overall_state = "degraded"
        label = "Degraded"
    else:
        overall_state = "down"
        label = "Down"

    detail = ", ".join(
        f"{component['name']} {component['label'].lower()}" for component in components
    )
    return {
        "state": overall_state,
        "label": label,
        "detail": detail,
        "components": components,
    }
