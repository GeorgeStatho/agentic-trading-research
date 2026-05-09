from __future__ import annotations

from typing import Any

from alpaca.trading.client import TradingClient

from .clients import ALPACA_PAPER, get_default_trading_client
from .utils import safe_float


def GetAccountDiagnostics(
    *,
    trading_client_override: TradingClient | None = None,
    echo: bool = True,
) -> dict[str, Any]:
    """Return the account fields this project uses most often."""
    active_trading_client = trading_client_override or get_default_trading_client()
    account = active_trading_client.get_account()

    diagnostics = {
        "account_number": str(getattr(account, "account_number", "") or ""),
        "status": str(getattr(account, "status", "") or ""),
        "buying_power": safe_float(getattr(account, "buying_power", None)),
        "options_buying_power": safe_float(getattr(account, "options_buying_power", None)),
        "cash": safe_float(getattr(account, "cash", None)),
        "equity": safe_float(getattr(account, "equity", None)),
        "portfolio_value": safe_float(getattr(account, "portfolio_value", None)),
        "daytrading_buying_power": safe_float(getattr(account, "daytrading_buying_power", None)),
        "regt_buying_power": safe_float(getattr(account, "regt_buying_power", None)),
        "trading_blocked": getattr(account, "trading_blocked", None),
        "account_blocked": getattr(account, "account_blocked", None),
        "shorting_enabled": getattr(account, "shorting_enabled", None),
        "options_approved_level": getattr(account, "options_approved_level", None),
        "options_trading_level": getattr(account, "options_trading_level", None),
        "paper": ALPACA_PAPER,
    }

    if echo:
        for key, value in diagnostics.items():
            print(f"{key}: {value}")

    return diagnostics
