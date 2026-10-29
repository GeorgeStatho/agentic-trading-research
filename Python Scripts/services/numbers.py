from __future__ import annotations

from typing import Any


def safe_float(value: Any) -> float | None:
    """Convert a value to float when possible."""
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
