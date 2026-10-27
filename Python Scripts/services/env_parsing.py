"""Read typed values from environment variables, falling back to the default on bad input."""
from __future__ import annotations

import os


def env_flag(name: str, default: bool) -> bool:
    """Read a boolean-like environment variable."""
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


def env_percentage(name: str, default: float) -> float:
    """Read a 0..100 percentage environment variable."""
    raw_value = str(os.getenv(name, str(default))).strip()
    try:
        parsed = float(raw_value)
    except ValueError:
        parsed = float(default)
    return min(100.0, max(0.0, parsed))


def env_positive_int(name: str, default: int) -> int:
    """Read a positive integer environment variable."""
    raw_value = str(os.getenv(name, str(default))).strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        parsed = int(default)
    return max(1, parsed)


def env_optional_positive_int(name: str) -> int | None:
    """Read an optional positive integer environment variable."""
    raw_value = str(os.getenv(name, "")).strip()
    if not raw_value:
        return None
    try:
        parsed = int(raw_value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def env_non_negative_int(name: str, default: int) -> int:
    """Read a non-negative integer environment variable."""
    raw_value = str(os.getenv(name, str(default))).strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        parsed = int(default)
    return max(0, parsed)


def env_float(name: str, default: float) -> float:
    """Read a float environment variable."""
    raw_value = str(os.getenv(name, str(default))).strip()
    try:
        return float(raw_value)
    except ValueError:
        return float(default)
