"""The per-share price an option contract is sized and capped at: the quote's ask, then mid, then bid, then the last trade or close."""
from __future__ import annotations

from typing import Any

from services.core.numbers import safe_float


def option_reference_price(contract: dict[str, Any]) -> float:
    latest_quote = contract.get("latest_quote", {})
    if isinstance(latest_quote, dict):
        for key in ("ask_price", "midpoint_price", "bid_price"):
            price = safe_float(latest_quote.get(key))
            if price is not None and price > 0:
                return price

    for key in ("latest_trade_price", "close_price"):
        price = safe_float(contract.get(key))
        if price is not None and price > 0:
            return price

    return 0.0
