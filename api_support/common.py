from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import jsonify

from api_support.context import OPTION_POSITION_SETTINGS, logger


def safe_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_dte_exit_rule_configs() -> list[dict]:
    return [
        {
            "label": rule.label,
            "take_profit_pct": rule.take_profit_pct,
            "stop_loss_pct": rule.stop_loss_pct,
            "force_exit_days_to_expiration": rule.force_exit_days_to_expiration,
        }
        for rule in OPTION_POSITION_SETTINGS.dte_rules
    ]


def trim_text(value: str | None, limit: int) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        return ""
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 1)].rstrip()}..."


def safe_json_loads(value: str | None) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def internal_error(message: str, exc: Exception):
    logger.exception(message, exc_info=exc)
    return jsonify({"error": message}), 500


def safe_int(value, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default

    normalized = max(minimum, normalized)
    if maximum is not None:
        normalized = min(maximum, normalized)
    return normalized


def read_json_payload(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def parse_datetime(value: str | None):
    if not value:
        return None

    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_json_file(path: Path) -> tuple[dict, int]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return jsonify(json.load(handle)), 200
    except FileNotFoundError:
        return jsonify({"error": f"File not found: {path.name}"}), 404
    except Exception as exc:
        return internal_error(f"Failed to read {path.name}.", exc)
