from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import safe_float
from services.option_position_state import get_position_state, update_position_state

from ._option_positions_defaults import (
    DEFAULT_OPTION_POSITION_STATE_PATH,
    MomentumHistoryConfig,
    PENDING_ORDER_CLEAR_STATUSES,
    PENDING_ORDER_OPEN_STATUSES,
    PendingExitConfig,
)


def _resolve_option_position_state_path() -> Path:
    return Path(os.getenv("OPTION_POSITION_STATE_PATH", str(DEFAULT_OPTION_POSITION_STATE_PATH)))


def _resolve_pending_order_id(symbol: str, order_summary: dict[str, Any]) -> str:
    order_id = str(order_summary.get("id") or "").strip()
    if order_id:
        return order_id
    submitted_at = str(order_summary.get("submitted_at") or datetime.now().isoformat()).strip()
    return f"pending:{symbol}:{submitted_at}"


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    candidate = raw_value[:-1] + "+00:00" if raw_value.endswith("Z") else raw_value
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _normalize_order_status(status: Any) -> str:
    return str(status or "").strip().lower()


def _normalize_momentum_history(history: Any) -> list[dict[str, Any]]:
    if not isinstance(history, list):
        return []
    normalized_history: list[dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().lower()
        if status not in {"good", "bad", "unknown"}:
            continue
        normalized_history.append(
            {
                "status": status,
                "checked_at": str(entry.get("checked_at") or "").strip(),
                "pnl_pct": safe_float(entry.get("pnl_pct")),
                "max_pnl_pct": safe_float(entry.get("max_pnl_pct")),
            }
        )
    return normalized_history


def _count_consecutive_bad_momentum(history: list[dict[str, Any]]) -> int:
    consecutive_bad_count = 0
    for entry in reversed(history):
        if str(entry.get("status") or "").strip().lower() != "bad":
            break
        consecutive_bad_count += 1
    return consecutive_bad_count


def _evaluate_momentum_history_exit(
    *,
    state: dict[str, Any],
    momentum_history_config: MomentumHistoryConfig,
) -> tuple[bool, list[str]]:
    if not _coerce_bool(state.get("momentum_tracking_active"), False):
        return False, []
    sample_count = max(0, _safe_int(state.get("momentum_history_sample_count")) or 0)
    if sample_count < momentum_history_config.min_samples:
        return False, [
            f"Momentum history tracking is active but only {sample_count} sample(s) have been collected; waiting for {momentum_history_config.min_samples}.",
        ]
    bad_count = max(0, _safe_int(state.get("momentum_bad_count")) or 0)
    consecutive_bad_count = max(0, _safe_int(state.get("momentum_consecutive_bad_count")) or 0)
    if bad_count >= momentum_history_config.bad_count_exit_threshold:
        return True, [
            f"Momentum history exit triggered because {bad_count} of the last {sample_count} tracked sample(s) were bad."
        ]
    if consecutive_bad_count >= momentum_history_config.consecutive_bad_exit_threshold:
        return True, [
            f"Momentum history exit triggered because {consecutive_bad_count} bad momentum samples occurred consecutively."
        ]
    return False, [
        f"Momentum history tracked {bad_count} bad sample(s) and {consecutive_bad_count} consecutive bad sample(s); exit thresholds were not reached."
    ]


def _build_momentum_history_updates(
    *,
    existing_state: dict[str, Any],
    current_max_pnl_pct: float | None,
    unrealized_pl_pct_ratio: float | None,
    momentum_status: str,
    momentum_history_config: MomentumHistoryConfig,
) -> dict[str, Any]:
    normalized_status = str(momentum_status or "").strip().lower()
    momentum_tracking_active = (
        current_max_pnl_pct is not None
        and current_max_pnl_pct >= momentum_history_config.enable_after_pnl_pct
    )
    history = _normalize_momentum_history(existing_state.get("momentum_history"))
    checked_at = datetime.now().isoformat()
    if momentum_tracking_active and normalized_status in {"good", "bad"}:
        history.append(
            {
                "status": normalized_status,
                "checked_at": checked_at,
                "pnl_pct": unrealized_pl_pct_ratio,
                "max_pnl_pct": current_max_pnl_pct,
            }
        )
        history = history[-momentum_history_config.window_size :]
    bad_count = sum(1 for entry in history if entry.get("status") == "bad")
    consecutive_bad_count = _count_consecutive_bad_momentum(history)
    return {
        "momentum_tracking_active": momentum_tracking_active,
        "momentum_history": history,
        "momentum_history_sample_count": len(history),
        "momentum_bad_count": bad_count,
        "momentum_consecutive_bad_count": consecutive_bad_count,
        "last_momentum_status": normalized_status,
        "last_momentum_checked_at": checked_at,
    }


def _resolve_pending_order_age_seconds(
    existing_state: dict[str, Any],
    order_summary: dict[str, Any],
    *,
    checked_at: str,
) -> float | None:
    submitted_at = (
        order_summary.get("submitted_at")
        or existing_state.get("pending_order_submitted_at")
        or existing_state.get("last_order_submitted_at")
    )
    submitted_dt = _parse_iso_datetime(submitted_at)
    checked_dt = _parse_iso_datetime(checked_at)
    if submitted_dt is None or checked_dt is None:
        return None
    if submitted_dt.tzinfo is not None and checked_dt.tzinfo is None:
        checked_dt = checked_dt.replace(tzinfo=submitted_dt.tzinfo)
    elif submitted_dt.tzinfo is None and checked_dt.tzinfo is not None:
        submitted_dt = submitted_dt.replace(tzinfo=checked_dt.tzinfo)
    return round(max(0.0, (checked_dt - submitted_dt).total_seconds()), 3)


def _resolve_pending_fill_metrics(
    existing_state: dict[str, Any],
    order_summary: dict[str, Any],
) -> tuple[int, int, int]:
    requested_qty = max(
        0,
        _safe_int(order_summary.get("requested_qty"))
        or _safe_int(existing_state.get("pending_qty"))
        or _safe_int(order_summary.get("qty"))
        or 0,
    )
    filled_qty = max(0, _safe_int(order_summary.get("filled_qty")) or 0)
    if requested_qty > 0:
        filled_qty = min(filled_qty, requested_qty)
    remaining_qty = max(0, requested_qty - filled_qty) if requested_qty > 0 else 0
    return requested_qty, filled_qty, remaining_qty


def _reconcile_pending_order_state(
    *,
    state_path: Path,
    option_symbol: str,
    position_state: dict[str, Any] | None,
    resolve_order_status: Any | None,
    cancel_order: Any | None,
    broker_quantity: int | None,
    pending_exit_config: PendingExitConfig,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    existing_state = dict(position_state or {})
    reconciliation = {
        "pending_order_status": "",
        "pending_order_last_checked_at": "",
        "pending_order_terminal": False,
        "pending_order_error": "",
        "pending_order_age_seconds": None,
        "pending_order_stale": False,
        "pending_order_cancel_submitted": False,
        "pending_order_filled_qty": 0,
        "pending_order_remaining_qty": 0,
    }
    pending_order_id = str(existing_state.get("pending_order_id") or "").strip()
    if not pending_order_id or resolve_order_status is None:
        return position_state, reconciliation
    checked_at = datetime.now().isoformat()
    try:
        order_summary = resolve_order_status(pending_order_id)
    except Exception as exc:
        reconciliation["pending_order_error"] = str(exc)
        reconciliation["pending_order_last_checked_at"] = checked_at
        return position_state, reconciliation
    normalized_status = _normalize_order_status(order_summary.get("status"))
    _, filled_qty, remaining_qty = _resolve_pending_fill_metrics(existing_state, order_summary)
    order_age_seconds = _resolve_pending_order_age_seconds(existing_state, order_summary, checked_at=checked_at)
    pending_order_stale = (
        pending_exit_config.cancel_on_stale
        and order_age_seconds is not None
        and order_age_seconds >= max(0.0, pending_exit_config.stale_minutes) * 60.0
    )
    reconciliation["pending_order_status"] = normalized_status
    reconciliation["pending_order_last_checked_at"] = checked_at
    reconciliation["pending_order_age_seconds"] = order_age_seconds
    reconciliation["pending_order_stale"] = pending_order_stale
    reconciliation["pending_order_filled_qty"] = filled_qty
    reconciliation["pending_order_remaining_qty"] = remaining_qty
    updates: dict[str, Any] = {
        "pending_order_last_checked_at": checked_at,
        "last_reconciled_order_status": normalized_status,
        "pending_order_submitted_at": str(
            order_summary.get("submitted_at")
            or existing_state.get("pending_order_submitted_at")
            or existing_state.get("last_order_submitted_at")
            or ""
        ),
        "pending_filled_qty": filled_qty,
        "pending_remaining_qty": remaining_qty,
    }
    if broker_quantity is not None:
        updates["last_seen_qty"] = int(broker_quantity)
        updates["remaining_qty"] = int(broker_quantity)
    if filled_qty > 0:
        fill_timestamp = str(order_summary.get("filled_at") or order_summary.get("updated_at") or checked_at)
        updates["last_partial_fill_at"] = fill_timestamp
        updates["last_filled_qty"] = filled_qty
    if normalized_status in PENDING_ORDER_OPEN_STATUSES and pending_order_stale:
        if cancel_order is None:
            reconciliation["pending_order_error"] = (
                "Pending exit order exceeded the stale timeout but no cancel callback was provided."
            )
            persisted_state = update_position_state(state_path, option_symbol, updates)
            return persisted_state, reconciliation
        try:
            cancel_summary = cancel_order(pending_order_id)
            reconciliation["pending_order_cancel_submitted"] = True
            updates["last_cancel_attempt_at"] = checked_at
            order_summary = {**order_summary, **(cancel_summary or {})}
            normalized_status = _normalize_order_status(order_summary.get("status"))
            _, filled_qty, remaining_qty = _resolve_pending_fill_metrics(existing_state, order_summary)
            reconciliation["pending_order_status"] = normalized_status
            reconciliation["pending_order_filled_qty"] = filled_qty
            reconciliation["pending_order_remaining_qty"] = remaining_qty
            updates["last_reconciled_order_status"] = normalized_status
            updates["pending_filled_qty"] = filled_qty
            updates["pending_remaining_qty"] = remaining_qty
            if filled_qty > 0:
                fill_timestamp = str(order_summary.get("filled_at") or order_summary.get("updated_at") or checked_at)
                updates["last_partial_fill_at"] = fill_timestamp
                updates["last_filled_qty"] = filled_qty
        except Exception as exc:
            reconciliation["pending_order_error"] = str(exc)
            persisted_state = update_position_state(state_path, option_symbol, updates)
            return persisted_state, reconciliation
    if normalized_status in PENDING_ORDER_OPEN_STATUSES:
        persisted_state = update_position_state(state_path, option_symbol, updates)
        return persisted_state, reconciliation
    if normalized_status in PENDING_ORDER_CLEAR_STATUSES:
        pending_reason = str(existing_state.get("pending_reason") or "").strip()
        pending_action = str(existing_state.get("pending_action") or "").strip()
        updates.update(
            {
                "pending_order_id": "",
                "pending_action": "",
                "pending_qty": 0,
                "pending_reason": "",
                "pending_order_submitted_at": "",
                "pending_filled_qty": 0,
                "pending_remaining_qty": 0,
            }
        )
        if normalized_status == "filled":
            updates["last_filled_at"] = str(order_summary.get("filled_at") or order_summary.get("updated_at") or checked_at)
            updates["last_filled_qty"] = filled_qty or (_safe_int(existing_state.get("pending_qty")) or 0)
        elif filled_qty > 0:
            updates["last_filled_at"] = str(order_summary.get("filled_at") or order_summary.get("updated_at") or checked_at)
        elif pending_reason == "first_scale_out_trigger":
            updates["took_first_scale_out"] = False
        elif pending_reason == "second_scale_out_trigger":
            updates["took_second_scale_out"] = False
        if normalized_status in {"canceled", "cancelled", "expired", "rejected"}:
            updates["last_retryable_exit_status"] = normalized_status
            updates["last_retryable_exit_reason"] = pending_reason
            updates["last_retryable_exit_action"] = pending_action
            updates["last_retryable_exit_at"] = checked_at
        persisted_state = update_position_state(state_path, option_symbol, updates)
        reconciliation["pending_order_terminal"] = True
        return persisted_state, reconciliation
    persisted_state = update_position_state(state_path, option_symbol, updates)
    return persisted_state, reconciliation


def _mark_exit_submission(
    *,
    state_path: Path,
    option_symbol: str,
    summary: dict[str, Any],
    order_summary: dict[str, Any],
) -> None:
    existing_state = get_position_state(state_path, option_symbol) or {}
    requested_qty = _safe_int(order_summary.get("requested_qty")) or _safe_int(order_summary.get("qty")) or 0
    pending_order_id = _resolve_pending_order_id(option_symbol, order_summary)
    pending_updates: dict[str, Any] = {
        "pending_order_id": pending_order_id,
        "pending_action": str(summary.get("exit_action") or ""),
        "pending_qty": requested_qty,
        "pending_reason": str(summary.get("exit_reason") or ""),
        "last_order_submitted_at": str(order_summary.get("submitted_at") or datetime.now().isoformat()),
        "pending_order_submitted_at": str(order_summary.get("submitted_at") or datetime.now().isoformat()),
        "pending_filled_qty": 0,
        "pending_remaining_qty": requested_qty,
        "last_action": str(summary.get("exit_action") or ""),
    }
    if (
        str(existing_state.get("last_retryable_exit_reason") or "").strip() != str(summary.get("exit_reason") or "").strip()
        or str(existing_state.get("last_retryable_exit_action") or "").strip() != str(summary.get("exit_action") or "").strip()
    ):
        pending_updates["last_retryable_exit_reason"] = ""
        pending_updates["last_retryable_exit_action"] = ""
        pending_updates["last_retryable_exit_status"] = ""
        pending_updates["last_retryable_exit_at"] = ""
    if summary.get("exit_reason") == "first_scale_out_trigger":
        pending_updates["took_first_scale_out"] = True
    if summary.get("exit_reason") == "second_scale_out_trigger":
        pending_updates["took_second_scale_out"] = True
    persisted_state = update_position_state(state_path, option_symbol, pending_updates)
    summary["pending_order_id"] = pending_order_id
    summary["pending_order_status"] = str(order_summary.get("status") or "")
    summary["pending_order_last_checked_at"] = str(order_summary.get("submitted_at") or "")
    summary["pending_order_terminal"] = False
    summary["pending_order_error"] = ""
    summary["pending_order_age_seconds"] = 0.0
    summary["pending_order_stale"] = False
    summary["pending_order_cancel_submitted"] = False
    summary["pending_order_filled_qty"] = 0
    summary["pending_order_remaining_qty"] = requested_qty
    summary["last_action"] = str(persisted_state.get("last_action") or summary.get("exit_action") or "")
    summary["last_action_at"] = str(persisted_state.get("updated_at") or "")
    summary["took_first_scale_out"] = bool(persisted_state.get("took_first_scale_out"))
    summary["took_second_scale_out"] = bool(persisted_state.get("took_second_scale_out"))
