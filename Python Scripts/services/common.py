from __future__ import annotations

import os
import re
from typing import Any


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


def safe_float(value: Any) -> float | None:
    """Convert a value to float when possible."""
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def looks_like_option_symbol(symbol: str, asset_class: str = "") -> bool:
    """Match Alpaca option positions by asset class or OCC-style symbol."""
    normalized_asset_class = str(asset_class or "").strip().lower()
    if "option" in normalized_asset_class:
        return True
    return bool(re.fullmatch(r"[A-Z]+\d{6}[CP]\d{8}", str(symbol or "").strip().upper()))


def load_exit_hours_to_expiration() -> float:
    """Preserve the existing env fallback from hours to days."""
    raw_value = str(
        os.getenv(
            "OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION",
            os.getenv("OPTION_POSITION_EXIT_DAYS_TO_EXPIRATION", "1"),
        )
    ).strip()
    hours_value = float(raw_value)
    multiplier = 1.0 if os.getenv("OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION") is not None else 24.0
    return max(0.0, hours_value * multiplier)
