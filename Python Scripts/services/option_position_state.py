from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


STATE_SCHEMA_VERSION = 1

__all__ = [
    "STATE_SCHEMA_VERSION",
    "build_empty_state",
    "delete_position_state",
    "get_position_state",
    "load_state",
    "save_state",
    "update_position_state",
]


def _now_iso() -> str:
    return datetime.now().isoformat()


def _normalize_option_symbol(option_symbol: str) -> str:
    return str(option_symbol or "").strip().upper()


def build_empty_state() -> dict[str, Any]:
    timestamp = _now_iso()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": timestamp,
        "positions": {},
    }


def _normalize_loaded_state(payload: Any) -> dict[str, Any]:
    empty_state = build_empty_state()
    if not isinstance(payload, dict):
        return empty_state

    positions = payload.get("positions")
    normalized_positions = positions if isinstance(positions, dict) else {}
    normalized_state = {
        "schema_version": int(payload.get("schema_version") or STATE_SCHEMA_VERSION),
        "updated_at": str(payload.get("updated_at") or empty_state["updated_at"]),
        "positions": {},
    }

    for raw_symbol, raw_position_state in normalized_positions.items():
        symbol = _normalize_option_symbol(raw_symbol)
        if not symbol or not isinstance(raw_position_state, dict):
            continue
        normalized_state["positions"][symbol] = dict(raw_position_state)

    return normalized_state


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return build_empty_state()
    except json.JSONDecodeError:
        return build_empty_state()

    return _normalize_loaded_state(raw_payload)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")

    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temp_path, path)


def save_state(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_state = _normalize_loaded_state(payload)
    normalized_state["updated_at"] = _now_iso()
    _atomic_write_json(path, normalized_state)
    return normalized_state


def get_position_state(path: Path, option_symbol: str) -> dict[str, Any] | None:
    state = load_state(path)
    symbol = _normalize_option_symbol(option_symbol)
    if not symbol:
        return None

    position_state = state["positions"].get(symbol)
    return dict(position_state) if isinstance(position_state, dict) else None


def update_position_state(path: Path, option_symbol: str, updates: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(updates, dict):
        raise TypeError("updates must be a dict")

    symbol = _normalize_option_symbol(option_symbol)
    if not symbol:
        raise ValueError("option_symbol must not be empty")

    state = load_state(path)
    existing_position_state = state["positions"].get(symbol)
    normalized_position_state = (
        dict(existing_position_state) if isinstance(existing_position_state, dict) else {}
    )
    if "opened_at" not in normalized_position_state:
        normalized_position_state["opened_at"] = _now_iso()

    normalized_position_state.update(updates)
    normalized_position_state["entry_option_symbol"] = symbol
    normalized_position_state["updated_at"] = _now_iso()
    state["positions"][symbol] = normalized_position_state
    save_state(path, state)
    return dict(normalized_position_state)


def delete_position_state(path: Path, option_symbol: str) -> bool:
    symbol = _normalize_option_symbol(option_symbol)
    if not symbol:
        return False

    state = load_state(path)
    if symbol not in state["positions"]:
        return False

    del state["positions"][symbol]
    save_state(path, state)
    return True
