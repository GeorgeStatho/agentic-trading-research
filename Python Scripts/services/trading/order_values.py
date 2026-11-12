from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)

# The lifecycle phase of a broker order: every module that waits on an order branches on these.
WORKING, FILLED, ENDED, REPLACED = "working", "filled", "ended", "replaced"
WORKING_STATUSES = frozenset({
    "accepted", "accepted_for_bidding", "calculated", "held", "new", "partially_filled",
    "pending_cancel", "pending_new", "pending_replace", "pending_review", "stopped", "suspended",
})
# An ended order can still carry a partial fill; callers read filled_qty for that.
ENDED_STATUSES = frozenset({"canceled", "cancelled", "expired", "rejected", "done_for_day"})
_warned_statuses: set[str] = set()


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


def order_phase(status: Any) -> str:
    """The lifecycle phase of a broker order status; an unknown status keeps the order working."""
    normalized = normalize_order_status(status)
    if normalized == FILLED:
        return FILLED
    if normalized == REPLACED:
        return REPLACED
    if normalized in ENDED_STATUSES:
        return ENDED
    if normalized not in WORKING_STATUSES and normalized not in _warned_statuses:
        _warned_statuses.add(normalized)
        LOGGER.warning("Unknown broker order status %r; treating the order as still working.", normalized)
    return WORKING


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
