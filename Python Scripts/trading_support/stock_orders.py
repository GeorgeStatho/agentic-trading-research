from __future__ import annotations

from dataclasses import dataclass

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from .clients import get_default_trading_client


_TIME_IN_FORCE_BY_LABEL = {
    "DAY": TimeInForce.DAY,
    "FOK": TimeInForce.FOK,
    "GTC": TimeInForce.GTC,
    "IOC": TimeInForce.IOC,
}


def _resolve_time_in_force(value: str) -> TimeInForce:
    normalized_value = str(value or "").strip().upper()
    if normalized_value not in _TIME_IN_FORCE_BY_LABEL:
        supported_values = ", ".join(sorted(_TIME_IN_FORCE_BY_LABEL))
        raise ValueError(f"Unsupported timeInForce '{value}'. Expected one of: {supported_values}.")
    return _TIME_IN_FORCE_BY_LABEL[normalized_value]


@dataclass
class StockTrades:
    """Small stateful helper for simple stock buy/sell submissions."""

    company: str
    numOfStocks: float
    timeInForce: str
    limit: bool

    def __post_init__(self) -> None:
        self.company = str(self.company or "").strip().upper()
        self.numOfStocks = float(self.numOfStocks or 0)

    def ImmediateStockBuy(self, numOfStocks: int = 0) -> None:
        self._submit_market_order(qty=numOfStocks, side=OrderSide.BUY)
        self.numOfStocks += numOfStocks

    def StockAtPriceBuy(self, numOfStocks: int = 0, limit_price: int = 0, notional: int = 0) -> None:
        self._submit_limit_order(
            qty=numOfStocks,
            side=OrderSide.BUY,
            limit_price=limit_price,
            notional=notional,
        )
        self.numOfStocks += numOfStocks

    def ImmediateStockSell(self, numOfStocks: int = 0) -> None:
        self._submit_market_order(qty=numOfStocks, side=OrderSide.SELL)
        self.numOfStocks -= numOfStocks

    def StockAtPriceSell(self, numOfStocks: int = 0, limit_price: int = 0, notional: int = 0) -> None:
        self._submit_limit_order(
            qty=numOfStocks,
            side=OrderSide.SELL,
            limit_price=limit_price,
            notional=notional,
        )
        self.numOfStocks -= numOfStocks

    def _submit_market_order(self, *, qty: int, side: OrderSide) -> None:
        market_order_data = MarketOrderRequest(
            symbol=self.company,
            qty=qty,
            side=side,
            time_in_force=_resolve_time_in_force(self.timeInForce),
        )
        get_default_trading_client().submit_order(order_data=market_order_data)

    def _submit_limit_order(
        self,
        *,
        qty: int,
        side: OrderSide,
        limit_price: int,
        notional: int,
    ) -> None:
        limit_order_data = LimitOrderRequest(
            symbol=self.company,
            limit_price=limit_price,
            notional=notional,
            qty=qty,
            side=side,
            time_in_force=_resolve_time_in_force(self.timeInForce),
        )
        get_default_trading_client().submit_order(order_data=limit_order_data)
