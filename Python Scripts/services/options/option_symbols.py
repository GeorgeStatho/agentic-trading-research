"""OCC-style option symbol helpers."""
from __future__ import annotations

import re


def looks_like_option_symbol(symbol: str, asset_class: str = "") -> bool:
    """Match Alpaca option positions by asset class or OCC-style symbol."""
    normalized_asset_class = str(asset_class or "").strip().lower()
    if "option" in normalized_asset_class:
        return True
    return bool(re.fullmatch(r"[A-Z]+\d{6}[CP]\d{8}", str(symbol or "").strip().upper()))


def option_underlying_symbol(symbol: str) -> str:
    """Extract the underlying ticker from a standard OCC option symbol."""
    match = re.fullmatch(r"([A-Z]+)\d{6}[CP]\d{8}", str(symbol or "").strip().upper())
    return match.group(1) if match is not None else ""
