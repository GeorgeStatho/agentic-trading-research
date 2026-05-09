from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from alpaca.data import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_DIR / ".env")

API_KEY = os.getenv("PUBLIC_KEY")
API_SECRET_KEY = str(os.getenv("PRIVATE_KEY") or "")
ALPACA_PAPER = str(os.getenv("ALPACA_PAPER", "true")).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
    "",
}


def InitializeTradingClient(api_key: str, secret: str, paper: bool) -> TradingClient:
    """Create an Alpaca trading client using key/secret auth only."""
    os.environ.pop("APCA_OAUTH_TOKEN", None)
    os.environ.pop("ALPACA_OAUTH_TOKEN", None)
    return TradingClient(api_key=api_key, secret_key=secret, oauth_token=None, paper=paper)


def IntializeTradingClient(api_key: str, secret: str, paper: bool) -> TradingClient:
    """Backward-compatible alias for the original misspelled helper name."""
    return InitializeTradingClient(api_key=api_key, secret=secret, paper=paper)


@lru_cache(maxsize=1)
def get_default_trading_client() -> TradingClient:
    return InitializeTradingClient(API_KEY, API_SECRET_KEY, ALPACA_PAPER)


@lru_cache(maxsize=1)
def get_option_history_client() -> OptionHistoricalDataClient:
    return OptionHistoricalDataClient(API_KEY, API_SECRET_KEY)


@lru_cache(maxsize=1)
def get_stock_history_client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(API_KEY, API_SECRET_KEY)


trading_client = get_default_trading_client()
option_history_client = get_option_history_client()
stock_history_client = get_stock_history_client()
