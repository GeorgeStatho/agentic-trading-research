from __future__ import annotations

from typing import Any


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def mid_price(bid_price: float | None, ask_price: float | None) -> float | None:
    if bid_price is None or ask_price is None:
        return None
    return round((bid_price + ask_price) / 2.0, 4)
