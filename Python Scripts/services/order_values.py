from __future__ import annotations

from typing import Any


def _normalize_enum_value(value: Any, *prefixes: str) -> str:
    """Return a stable lower-case value from an SDK enum or legacy string."""
    raw_value = getattr(value, "value", value)
    normalized = str(raw_value or "").strip().lower()
    for prefix in prefixes:
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def normalize_order_status(value: Any) -> str:
    return _normalize_enum_value(value, "orderstatus.", "order_status.")


def normalize_order_side(value: Any) -> str:
    return _normalize_enum_value(value, "orderside.", "order_side.")


def normalize_order_type(value: Any) -> str:
    return _normalize_enum_value(value, "ordertype.", "order_type.")


def normalize_time_in_force(value: Any) -> str:
    return _normalize_enum_value(value, "timeinforce.", "time_in_force.")


def normalize_order_summary(order_summary: dict[str, Any]) -> dict[str, Any]:
    """Copy an order summary with all persisted enum fields canonicalized."""
    normalized = dict(order_summary or {})
    normalized["status"] = normalize_order_status(normalized.get("status"))
    normalized["side"] = normalize_order_side(normalized.get("side"))
    normalized["type"] = normalize_order_type(normalized.get("type"))
    normalized["time_in_force"] = normalize_time_in_force(normalized.get("time_in_force"))
    return normalized
