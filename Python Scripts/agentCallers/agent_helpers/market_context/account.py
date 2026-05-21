from __future__ import annotations

import re
from typing import Any

from .common import (
    ALPACA_IMPORT_ERROR,
    OPTION_SYMBOL_TEMPLATE,
    _get_alpaca_clients,
    _get_field,
    _safe_float,
    _serialize_scalar,
)


def _serialize_position(position: Any) -> dict[str, Any]:
    return {
        "symbol": str(_get_field(position, "symbol") or ""),
        "asset_class": _serialize_scalar(_get_field(position, "asset_class")) or "",
        "side": _serialize_scalar(_get_field(position, "side")) or "",
        "qty": _safe_float(_get_field(position, "qty")),
        "avg_entry_price": _safe_float(_get_field(position, "avg_entry_price")),
        "current_price": _safe_float(_get_field(position, "current_price")),
        "market_value": _safe_float(_get_field(position, "market_value")),
        "cost_basis": _safe_float(_get_field(position, "cost_basis")),
        "unrealized_pl": _safe_float(_get_field(position, "unrealized_pl")),
        "unrealized_plpc": _safe_float(_get_field(position, "unrealized_plpc")),
        "change_today": _safe_float(_get_field(position, "change_today")),
    }


def _build_account_state(company_symbol: str) -> dict[str, Any]:
    unavailable = {
        "available": False,
        "buying_power": None,
        "options_buying_power": None,
        "cash": None,
        "equity": None,
        "portfolio_value": None,
        "trading_blocked": None,
        "account_blocked": None,
        "options_approved_level": None,
        "options_trading_level": None,
        "company_position_state": {
            "matching_position_count": 0,
            "has_stock_position": False,
            "has_option_positions": False,
            "positions": [],
        },
    }
    clients = _get_alpaca_clients()
    if clients is None:
        unavailable["error"] = (
            f"alpaca-py is unavailable: {ALPACA_IMPORT_ERROR}"
            if ALPACA_IMPORT_ERROR is not None
            else "Alpaca credentials were not configured."
        )
        return unavailable
    try:
        account = clients["trading"].get_account()
        positions = clients["trading"].get_all_positions()
    except Exception as exc:
        unavailable["error"] = str(exc)
        return unavailable

    option_pattern = re.compile(rf"^{re.escape(company_symbol)}{OPTION_SYMBOL_TEMPLATE}")
    matching_positions: list[dict[str, Any]] = []
    has_stock_position = False
    has_option_positions = False
    for position in positions or []:
        symbol = str(_get_field(position, "symbol") or "").strip().upper()
        if symbol == company_symbol:
            has_stock_position = True
            matching_positions.append(_serialize_position(position))
        elif option_pattern.match(symbol):
            has_option_positions = True
            matching_positions.append(_serialize_position(position))

    return {
        "available": True,
        "buying_power": _safe_float(_get_field(account, "buying_power")),
        "options_buying_power": _safe_float(_get_field(account, "options_buying_power")),
        "cash": _safe_float(_get_field(account, "cash")),
        "equity": _safe_float(_get_field(account, "equity")),
        "portfolio_value": _safe_float(_get_field(account, "portfolio_value")),
        "daytrading_buying_power": _safe_float(_get_field(account, "daytrading_buying_power")),
        "regt_buying_power": _safe_float(_get_field(account, "regt_buying_power")),
        "trading_blocked": _get_field(account, "trading_blocked"),
        "account_blocked": _get_field(account, "account_blocked"),
        "shorting_enabled": _get_field(account, "shorting_enabled"),
        "options_approved_level": _serialize_scalar(_get_field(account, "options_approved_level")),
        "options_trading_level": _serialize_scalar(_get_field(account, "options_trading_level")),
        "company_position_state": {
            "matching_position_count": len(matching_positions),
            "has_stock_position": has_stock_position,
            "has_option_positions": has_option_positions,
            "positions": matching_positions,
        },
    }
