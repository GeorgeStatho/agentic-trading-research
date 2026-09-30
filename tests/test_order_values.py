from __future__ import annotations

import unittest
from types import SimpleNamespace

from services.order_values import normalize_order_summary
from services.trading_gateway import AlpacaTradingGateway


class OrderValueTests(unittest.TestCase):
    def test_normalize_order_summary_accepts_sdk_enum_strings(self) -> None:
        summary = normalize_order_summary(
            {
                "status": "OrderStatus.FILLED",
                "side": "OrderSide.SELL",
                "type": "OrderType.LIMIT",
                "time_in_force": "TimeInForce.DAY",
            }
        )
        self.assertEqual(
            summary,
            {
                "status": "filled",
                "side": "sell",
                "type": "limit",
                "time_in_force": "day",
            },
        )

    def test_gateway_persists_plain_values_from_enum_objects(self) -> None:
        order = SimpleNamespace(
            id="order-1",
            symbol="XOM260814C00155000",
            asset_class="us_option",
            qty="1",
            side=SimpleNamespace(value="sell"),
            type=SimpleNamespace(value="limit"),
            time_in_force=SimpleNamespace(value="day"),
            status=SimpleNamespace(value="filled"),
            submitted_at="2026-07-27T14:20:00+00:00",
        )
        summary = AlpacaTradingGateway._serialize_order_summary(order, symbol=order.symbol, qty=1)
        self.assertEqual(summary["status"], "filled")
        self.assertEqual(summary["side"], "sell")
        self.assertEqual(summary["type"], "limit")
        self.assertEqual(summary["time_in_force"], "day")


if __name__ == "__main__":
    unittest.main()
