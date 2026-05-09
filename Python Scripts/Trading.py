"""Compatibility facade for trading helpers.

The implementation now lives in ``trading_support`` so stock order helpers,
option-position management, and Alpaca client bootstrapping stay separated by
responsibility.
"""

from trading_support import (
    ALPACA_PAPER,
    API_KEY,
    API_SECRET_KEY,
    DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION,
    DEFAULT_OPTION_STOP_LOSS_PCT,
    DEFAULT_OPTION_TAKE_PROFIT_PCT,
    OPTION_EXIT_DTE_RULES,
    CloseOptionPositions,
    GetAccountDiagnostics,
    GetCallOptionsForCompany,
    InitializeTradingClient,
    IntializeTradingClient,
    ManageCurrentOptionPositions,
    StockTrades,
    get_default_trading_client,
    get_option_history_client,
    get_stock_history_client,
    option_history_client,
    stock_history_client,
    trading_client,
)


__all__ = [
    "ALPACA_PAPER",
    "API_KEY",
    "API_SECRET_KEY",
    "CloseOptionPositions",
    "DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION",
    "DEFAULT_OPTION_STOP_LOSS_PCT",
    "DEFAULT_OPTION_TAKE_PROFIT_PCT",
    "GetAccountDiagnostics",
    "GetCallOptionsForCompany",
    "InitializeTradingClient",
    "IntializeTradingClient",
    "ManageCurrentOptionPositions",
    "OPTION_EXIT_DTE_RULES",
    "StockTrades",
    "get_default_trading_client",
    "get_option_history_client",
    "get_stock_history_client",
    "option_history_client",
    "stock_history_client",
    "trading_client",
]


if __name__ == "__main__":
    GetAccountDiagnostics()
