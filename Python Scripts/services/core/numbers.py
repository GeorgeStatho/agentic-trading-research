from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any) -> float | None:
    """Convert a value to float when possible."""
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def finite_float(value: Any) -> float | None:
    """Convert a value to a finite float; NaN and infinity become None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def safe_int(value: Any) -> int | None:
    """Convert a value to int with int(); text must be a whole number ("1.5" is None)."""
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
