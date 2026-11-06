from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from flask import Response, jsonify

from api_support.context import OPTION_POSITION_SETTINGS, logger


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


def read_json_file(path: Path) -> tuple[dict, int]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return jsonify(json.load(handle)), 200
    except FileNotFoundError:
        return jsonify({"error": f"File not found: {path.name}"}), 404
    except Exception as exc:
        return internal_error(f"Failed to read {path.name}.", exc)


# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_value(row: dict, column: str):
    value = row
    for key in column.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return "" if value is None else value


def csv_response(rows, columns, filename: str):
    """Rows as a CSV download. A column is a key or a dotted path into nested objects; absent values are blank."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows([_csv_value(row, column) for column in columns] for row in rows)
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})
