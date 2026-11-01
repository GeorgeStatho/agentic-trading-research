from __future__ import annotations

import logging
import os
from typing import Any

from services.core.numbers import safe_float
from services.options.option_symbols import looks_like_option_symbol, option_underlying_symbol
from services.trading.order_values import normalize_order_summary

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

    ALPACA_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - optional dependency
    TradingClient = Any
    OrderSide = None
    TimeInForce = None
    MarketOrderRequest = None
    LimitOrderRequest = None
    ALPACA_IMPORT_ERROR = exc

# Multi-leg support is newer than the rest of alpaca-py; only follower spreads need it, so an
# older SDK must not take production's single-leg trading down with it.
try:
    from alpaca.trading.enums import OrderClass, PositionIntent
    from alpaca.trading.requests import OptionLegRequest
except ImportError:  # pragma: no cover - optional dependency
    OrderClass = None
    PositionIntent = None
    OptionLegRequest = None


class AlpacaTradingGateway:
    """Encapsulate Alpaca-specific trading operations."""

    def __init__(
        self,
        *,
        paper: bool,
        logger: logging.Logger,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        self._paper = paper
        self._logger = logger
        self._api_key = api_key
        self._api_secret = api_secret

    def create_client(self) -> TradingClient:
        if ALPACA_IMPORT_ERROR is not None:
            raise RuntimeError(
                "alpaca-py is required to run the trading entrypoint. "
                f"Original import error: {ALPACA_IMPORT_ERROR}"
            )

        # Only an account built without keys uses production's; a follower must never fall back to them.
        api_key = str((os.getenv("PUBLIC_KEY") if self._api_key is None else self._api_key) or "").strip()
        api_secret = str((os.getenv("PRIVATE_KEY") if self._api_secret is None else self._api_secret) or "").strip()
        if not api_key or not api_secret:
            raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in .env")

        os.environ.pop("APCA_OAUTH_TOKEN", None)
        os.environ.pop("ALPACA_OAUTH_TOKEN", None)
        return TradingClient(api_key=api_key, secret_key=api_secret, oauth_token=None, paper=self._paper)

    def get_market_clock(self, trading_client: TradingClient) -> Any | None:
        try:
            return trading_client.get_clock()
        except Exception as exc:
            self._logger.exception("Failed to fetch Alpaca market clock: %s", exc)
            return None

    def market_is_open(self, trading_client: TradingClient, *, clock: Any | None = None) -> bool:
        try:
            clock = clock if clock is not None else self.get_market_clock(trading_client)
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
        positions = self.get_open_option_positions(trading_client)
        return sum(float(position["notional"]) for position in positions), len(positions)

    def get_open_option_positions(self, trading_client: TradingClient) -> list[dict[str, Any]]:
        """Return normalized broker option positions for deterministic entry guardrails."""
        positions = trading_client.get_all_positions()
        normalized_positions: list[dict[str, Any]] = []
        for position in positions or []:
            option_symbol = str(getattr(position, "symbol", "") or "").strip().upper()
            asset_class = str(getattr(position, "asset_class", "") or "")
            if not looks_like_option_symbol(option_symbol, asset_class):
                continue
            exposure_value = safe_float(getattr(position, "market_value", None))
            if exposure_value is None:
                exposure_value = safe_float(getattr(position, "cost_basis", None))
            normalized_positions.append(
                {
                    "option_symbol": option_symbol,
                    "underlying_symbol": str(getattr(position, "underlying_symbol", "") or "").strip().upper() or option_underlying_symbol(option_symbol),
                    "notional": abs(exposure_value or 0.0),
                    "quantity": abs(safe_float(getattr(position, "qty", None)) or 0.0),
                }
            )
        return normalized_positions

    def submit_option_market_order(
        self,
        trading_client: TradingClient,
        *,
        option_symbol: str,
        qty: int,
    ) -> dict[str, Any]:
        # Keep broker-specific order construction here, then normalize the
        # response so the service layer can treat order results as plain data.
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

    def submit_multileg_market_order(
        self,
        trading_client: TradingClient,
        *,
        legs: list[dict[str, Any]],
        qty: int,
        opening: bool = True,
    ) -> dict[str, Any]:
        """Submit an options spread as one multi-leg order; each leg is {symbol, side, ratio_qty}."""
        if MarketOrderRequest is None or OptionLegRequest is None:
            raise RuntimeError("alpaca-py is required to submit multi-leg option orders.")
        if int(qty) <= 0:
            raise ValueError("qty must be greater than zero.")
        intents = {
            ("buy", True): PositionIntent.BUY_TO_OPEN,
            ("sell", True): PositionIntent.SELL_TO_OPEN,
            ("buy", False): PositionIntent.BUY_TO_CLOSE,
            ("sell", False): PositionIntent.SELL_TO_CLOSE,
        }
        order_legs = [
            OptionLegRequest(
                symbol=str(leg["symbol"]).strip().upper(),
                ratio_qty=int(leg.get("ratio_qty") or 1),
                side=OrderSide(leg["side"]),
                position_intent=intents[(leg["side"], opening)],
            )
            for leg in legs
        ]
        order_request = MarketOrderRequest(
            qty=int(qty),
            order_class=OrderClass.MLEG,
            time_in_force=TimeInForce.DAY,
            legs=order_legs,
        )
        order = trading_client.submit_order(order_data=order_request)
        return self._serialize_order_summary(order=order, symbol="", qty=int(qty))

    def submit_stock_market_order(
        self,
        trading_client: TradingClient,
        *,
        symbol: str,
        qty: int,
        side: str,
    ) -> dict[str, Any]:
        if MarketOrderRequest is None or OrderSide is None or TimeInForce is None:
            raise RuntimeError("alpaca-py is required to submit stock orders.")
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'.")
        if int(qty) <= 0:
            raise ValueError("qty must be greater than zero.")
        normalized_symbol = str(symbol or "").strip().upper()
        order_request = MarketOrderRequest(
            symbol=normalized_symbol,
            qty=int(qty),
            side=OrderSide(side),
            time_in_force=TimeInForce.DAY,
        )
        order = trading_client.submit_order(order_data=order_request)
        return self._serialize_order_summary(order=order, symbol=normalized_symbol, qty=int(qty))

    @staticmethod
    def _serialize_order_summary(order: Any, *, symbol: str, qty: int) -> dict[str, Any]:
        return normalize_order_summary({
            "id": str(getattr(order, "id", "")),
            "symbol": str(getattr(order, "symbol", symbol)),
            "asset_class": str(getattr(order, "asset_class", "")),
            "qty": str(getattr(order, "qty", qty)),
            "side": getattr(order, "side", ""),
            "type": getattr(order, "type", ""),
            "time_in_force": getattr(order, "time_in_force", ""),
            "status": getattr(order, "status", ""),
            "submitted_at": str(getattr(order, "submitted_at", "")),
            "limit_price": str(getattr(order, "limit_price", "")),
        })

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

    def submit_option_sell_limit_order(
        self,
        trading_client: TradingClient,
        *,
        option_symbol: str,
        limit_price: float,
        qty: int | None = None,
    ) -> dict[str, Any]:
        if LimitOrderRequest is None or OrderSide is None or TimeInForce is None:
            raise RuntimeError("alpaca-py is required to submit option orders.")

        normalized_symbol = str(option_symbol or "").strip().upper()
        if not normalized_symbol:
            raise ValueError("option_symbol must not be empty.")
        limit_price_value = safe_float(limit_price)
        if limit_price_value is None or limit_price_value <= 0:
            raise ValueError("limit_price must be a positive number.")

        open_position = trading_client.get_open_position(normalized_symbol)
        asset_class = str(getattr(open_position, "asset_class", "") or "")
        if not looks_like_option_symbol(normalized_symbol, asset_class):
            raise ValueError(f"{normalized_symbol} is not an open option position.")

        current_qty = safe_float(getattr(open_position, "qty", None))
        current_qty_int = int(current_qty or 0)
        if current_qty_int <= 0:
            raise RuntimeError(f"No open quantity is available to sell for {normalized_symbol}.")
        # Default to the full open quantity (used for full exits, where the caller
        # only knows the symbol); otherwise validate the requested partial size.
        sell_qty = current_qty_int if qty is None else int(qty)
        if sell_qty <= 0:
            raise ValueError("qty must be greater than zero.")
        if sell_qty > current_qty_int:
            raise ValueError(
                f"Requested sell quantity {sell_qty} exceeds the current open quantity {current_qty_int} for {normalized_symbol}."
            )

        order_request = LimitOrderRequest(
            symbol=normalized_symbol,
            qty=sell_qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=round(float(limit_price_value), 2),
        )
        order = trading_client.submit_order(order_data=order_request)
        order_summary = self._serialize_order_summary(order=order, symbol=normalized_symbol, qty=sell_qty)
        order_summary["requested_qty"] = sell_qty
        order_summary["position_qty_before_order"] = current_qty_int
        order_summary["limit_price"] = round(float(limit_price_value), 2)
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
        order_summary["replaced_by"] = str(getattr(order, "replaced_by", ""))
        order_summary["replaces"] = str(getattr(order, "replaces", ""))
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

    def get_option_order_history(self, trading_client, *, option_symbol: str) -> list[dict]:
        """Read all order pages, overlapping boundaries so tied timestamps survive."""
        from datetime import timedelta
        from alpaca.trading.requests import GetOrdersRequest
        from services.trading.outcome_reconciliation import parse_timestamp

        orders = {}
        after = None
        while True:
            batch = trading_client.get_orders(filter=GetOrdersRequest(
                status="all", symbols=[option_symbol], limit=500,
                direction="asc", after=after,
            ))
            new_count = 0
            for order in batch:
                summary = self._serialize_order_summary(order=order, symbol=option_symbol, qty=0)
                for field in ("filled_qty", "filled_avg_price", "filled_at"):
                    summary[field] = str(getattr(order, field, "") or "")
                if summary["id"] not in orders:
                    new_count += 1
                orders[summary["id"]] = summary
            if len(batch) < 500:
                return list(orders.values())
            cursor = parse_timestamp(getattr(batch[-1], "submitted_at", None))
            if cursor is None or new_count == 0:
                raise RuntimeError("Order history pagination did not advance; refusing incomplete history")
            after = cursor - timedelta(microseconds=1)

    def get_execution_market_snapshot(self, *, option_symbol, underlying_symbol):
        from trading_support._option_positions_market import _get_latest_option_quote, _get_latest_stock_price
        from trading_support.clients import get_option_history_client, get_stock_history_client
        return {"option": _get_latest_option_quote(option_symbol, get_option_history_client),
                "underlying": _get_latest_stock_price(underlying_symbol, get_stock_history_client)}

    def get_position_exit_iv(self, position):
        """Read the existing chain snapshot adapter for one expiration and strike."""
        from agent_helpers.market_context.option_chain import (
            OptionChainRequest, _get_alpaca_clients, _normalize_option_snapshot_map, _get_field, _safe_float,
        )
        clients = _get_alpaca_clients()
        if clients is None or OptionChainRequest is None:
            return None
        chain = clients['option'].get_option_chain(OptionChainRequest(
            underlying_symbol=position['underlying_symbol'], expiration_date=position['expiration_date'],
            strike_price_gte=position['strike'], strike_price_lte=position['strike']))
        snapshot = _normalize_option_snapshot_map(chain).get(position['symbol'])
        return _safe_float(_get_field(snapshot, 'implied_volatility')) if snapshot is not None else None
