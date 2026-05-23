from __future__ import annotations

import os
from functools import lru_cache

from alpaca.data import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from services.config import AlpacaSettings


def _get_alpaca_settings() -> AlpacaSettings:
    return AlpacaSettings.from_env()


_ALPACA_SETTINGS = _get_alpaca_settings()
ALPACA_PAPER = _ALPACA_SETTINGS.paper
API_KEY = _ALPACA_SETTINGS.api_key
API_SECRET_KEY = _ALPACA_SETTINGS.api_secret


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
    settings = _get_alpaca_settings()
    return InitializeTradingClient(settings.api_key, settings.api_secret, settings.paper)


@lru_cache(maxsize=1)
def get_option_history_client() -> OptionHistoricalDataClient:
    settings = _get_alpaca_settings()
    return OptionHistoricalDataClient(settings.api_key, settings.api_secret)


@lru_cache(maxsize=1)
def get_stock_history_client() -> StockHistoricalDataClient:
    settings = _get_alpaca_settings()
    return StockHistoricalDataClient(settings.api_key, settings.api_secret)


class _LazyClientProxy:
    def __init__(self, factory):
        self._factory = factory

    def __getattr__(self, name: str):
        return getattr(self._factory(), name)


trading_client = _LazyClientProxy(get_default_trading_client)
option_history_client = _LazyClientProxy(get_option_history_client)
stock_history_client = _LazyClientProxy(get_stock_history_client)
