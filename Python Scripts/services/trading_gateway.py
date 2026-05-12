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
        return self._serialize_order_summary(order=order, symbol=option_symbol, qty=qty)

    @staticmethod
    def _serialize_order_summary(order: Any, *, symbol: str, qty: int) -> dict[str, Any]:
        return {
            "id": str(getattr(order, "id", "")),
            "symbol": str(getattr(order, "symbol", symbol)),
            "asset_class": str(getattr(order, "asset_class", "")),
            "qty": str(getattr(order, "qty", qty)),
            "side": str(getattr(order, "side", "")),
            "type": str(getattr(order, "type", "")),
            "time_in_force": str(getattr(order, "time_in_force", "")),
            "status": str(getattr(order, "status", "")),
            "submitted_at": str(getattr(order, "submitted_at", "")),
        }

    def submit_option_sell_market_order(
        self,
        trading_client: TradingClient,
        *,
        option_symbol: str,
        qty: int,
    ) -> dict[str, Any]:
        if MarketOrderRequest is None or OrderSide is None or TimeInForce is None:
            raise RuntimeError("alpaca-py is required to submit option orders.")

        normalized_symbol = str(option_symbol or "").strip().upper()
        if not normalized_symbol:
            raise ValueError("option_symbol must not be empty.")
        if int(qty) <= 0:
            raise ValueError("qty must be greater than zero.")

        open_position = trading_client.get_open_position(normalized_symbol)
        asset_class = str(getattr(open_position, "asset_class", "") or "")
        if not looks_like_option_symbol(normalized_symbol, asset_class):
            raise ValueError(f"{normalized_symbol} is not an open option position.")

        current_qty = safe_float(getattr(open_position, "qty", None))
        current_qty_int = int(current_qty or 0)
        if current_qty_int <= 0:
            raise RuntimeError(f"No open quantity is available to sell for {normalized_symbol}.")
        if int(qty) > current_qty_int:
            raise ValueError(
                f"Requested sell quantity {qty} exceeds the current open quantity {current_qty_int} for {normalized_symbol}."
            )

        order_request = MarketOrderRequest(
            symbol=normalized_symbol,
            qty=int(qty),
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = trading_client.submit_order(order_data=order_request)
        order_summary = self._serialize_order_summary(order=order, symbol=normalized_symbol, qty=int(qty))
        order_summary["requested_qty"] = int(qty)
        order_summary["position_qty_before_order"] = current_qty_int
        return order_summary

    def close_option_position(
        self,
        trading_client: TradingClient,
        *,
        option_symbol: str,
    ) -> dict[str, Any]:
        normalized_symbol = str(option_symbol or "").strip().upper()
        if not normalized_symbol:
            raise ValueError("option_symbol must not be empty.")

        open_position = trading_client.get_open_position(normalized_symbol)
        asset_class = str(getattr(open_position, "asset_class", "") or "")
        if not looks_like_option_symbol(normalized_symbol, asset_class):
            raise ValueError(f"{normalized_symbol} is not an open option position.")

        current_qty = safe_float(getattr(open_position, "qty", None))
        current_qty_int = int(current_qty or 0)
        close_order = trading_client.close_position(normalized_symbol)
        order_summary = self._serialize_order_summary(order=close_order, symbol=normalized_symbol, qty=current_qty_int)
        order_summary["requested_qty"] = current_qty_int
        order_summary["position_qty_before_order"] = current_qty_int
        return order_summary

    def get_order_status_summary(
        self,
        trading_client: TradingClient,
        *,
        order_id: str,
    ) -> dict[str, Any]:
        normalized_order_id = str(order_id or "").strip()
        if not normalized_order_id:
            raise ValueError("order_id must not be empty.")

        order = trading_client.get_order_by_id(normalized_order_id)
        order_summary = self._serialize_order_summary(
            order=order,
            symbol=str(getattr(order, "symbol", "")),
            qty=int(safe_float(getattr(order, "qty", None)) or 0),
        )
        order_summary["filled_qty"] = str(getattr(order, "filled_qty", ""))
        order_summary["filled_avg_price"] = str(getattr(order, "filled_avg_price", ""))
        order_summary["filled_at"] = str(getattr(order, "filled_at", ""))
        order_summary["canceled_at"] = str(getattr(order, "canceled_at", ""))
        order_summary["failed_at"] = str(getattr(order, "failed_at", ""))
        order_summary["expired_at"] = str(getattr(order, "expired_at", ""))
        order_summary["updated_at"] = str(getattr(order, "updated_at", ""))
        return order_summary

    def cancel_order(
        self,
        trading_client: TradingClient,
        *,
        order_id: str,
    ) -> dict[str, Any]:
        normalized_order_id = str(order_id or "").strip()
        if not normalized_order_id:
            raise ValueError("order_id must not be empty.")

        if hasattr(trading_client, "cancel_order_by_id"):
            trading_client.cancel_order_by_id(normalized_order_id)
        elif hasattr(trading_client, "cancel_order"):
            trading_client.cancel_order(normalized_order_id)
        else:
            raise RuntimeError("Trading client does not expose an order-cancel method.")

        return self.get_order_status_summary(trading_client, order_id=normalized_order_id)
