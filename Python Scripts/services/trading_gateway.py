from __future__ import annotations

import logging
import os
from typing import Any

from services.common import looks_like_option_symbol, safe_float

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    ALPACA_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - optional dependency
    TradingClient = Any
    OrderSide = None
    TimeInForce = None
    MarketOrderRequest = None
    ALPACA_IMPORT_ERROR = exc


class AlpacaTradingGateway:
    """Encapsulate Alpaca-specific trading operations."""

    def __init__(self, *, paper: bool, logger: logging.Logger) -> None:
        self._paper = paper
        self._logger = logger

    def create_client(self) -> TradingClient:
        if ALPACA_IMPORT_ERROR is not None:
            raise RuntimeError(
                "alpaca-py is required to run the trading entrypoint. "
                f"Original import error: {ALPACA_IMPORT_ERROR}"
            )

        api_key = str(os.getenv("PUBLIC_KEY") or "").strip()
        api_secret = str(os.getenv("PRIVATE_KEY") or "").strip()
        if not api_key or not api_secret:
            raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in .env")

        os.environ.pop("APCA_OAUTH_TOKEN", None)
        os.environ.pop("ALPACA_OAUTH_TOKEN", None)
        return TradingClient(api_key=api_key, secret_key=api_secret, oauth_token=None, paper=self._paper)

    def market_is_open(self, trading_client: TradingClient) -> bool:
        try:
            clock = trading_client.get_clock()
            return bool(getattr(clock, "is_open", False))
        except Exception as exc:
            self._logger.exception("Failed to check Alpaca market clock: %s", exc)
            return False

    def get_available_buying_power(self, trading_client: TradingClient) -> float:
        account = trading_client.get_account()
        buying_power = safe_float(getattr(account, "buying_power", None))
        if buying_power is None:
            raise RuntimeError("Unable to read buying_power from Alpaca account.")
        return buying_power

    def get_open_option_exposure(self, trading_client: TradingClient) -> tuple[float, int]:
        positions = trading_client.get_all_positions()
        option_exposure = 0.0
        option_position_count = 0
        for position in positions or []:
            symbol = str(getattr(position, "symbol", "") or "").strip().upper()
            asset_class = str(getattr(position, "asset_class", "") or "")
            if not looks_like_option_symbol(symbol, asset_class):
                continue

            option_position_count += 1
            exposure_value = safe_float(getattr(position, "market_value", None))
            if exposure_value is None:
                exposure_value = safe_float(getattr(position, "cost_basis", None))
            option_exposure += abs(exposure_value or 0.0)

        return option_exposure, option_position_count

    def submit_option_market_order(
        self,
        trading_client: TradingClient,
        *,
        option_symbol: str,
        qty: int,
    ) -> dict[str, Any]:
        if MarketOrderRequest is None or OrderSide is None or TimeInForce is None:
            raise RuntimeError("alpaca-py is required to submit option orders.")

        order_request = MarketOrderRequest(
            symbol=option_symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = trading_client.submit_order(order_data=order_request)
        return {
            "id": str(getattr(order, "id", "")),
            "symbol": str(getattr(order, "symbol", option_symbol)),
            "asset_class": str(getattr(order, "asset_class", "")),
            "qty": str(getattr(order, "qty", qty)),
            "side": str(getattr(order, "side", "")),
            "type": str(getattr(order, "type", "")),
            "time_in_force": str(getattr(order, "time_in_force", "")),
            "status": str(getattr(order, "status", "")),
            "submitted_at": str(getattr(order, "submitted_at", "")),
        }
