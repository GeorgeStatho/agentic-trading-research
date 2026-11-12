from __future__ import annotations

import unittest
from types import SimpleNamespace

from services.trading import order_values
from services.trading.order_values import ENDED, FILLED, REPLACED, WORKING, normalize_order_summary, order_phase
from services.trading.trading_gateway import AlpacaTradingGateway


class OrderPhaseTests(unittest.TestCase):
    def test_every_broker_status_has_one_phase(self) -> None:
        phases = {
            WORKING: ("accepted", "accepted_for_bidding", "calculated", "held", "new", "partially_filled",
                      "pending_cancel", "pending_new", "pending_replace", "pending_review", "stopped", "suspended"),
            FILLED: ("filled",),
            ENDED: ("canceled", "cancelled", "expired", "rejected", "done_for_day"),
            REPLACED: ("replaced",),
        }
        for phase, statuses in phases.items():
            for status in statuses:
                with self.subTest(status=status):
                    self.assertEqual(order_phase(status), phase)

    def test_sdk_enum_forms_are_normalized(self) -> None:
        self.assertEqual(order_phase("OrderStatus.REPLACED"), REPLACED)
        self.assertEqual(order_phase(SimpleNamespace(value="done_for_day")), ENDED)

    def test_unknown_status_keeps_the_order_working_and_warns_once(self) -> None:
        order_values._warned_statuses.discard("mystery")
        with self.assertLogs("services.trading.order_values", level="WARNING") as logs:
            self.assertEqual(order_phase("mystery"), WORKING)
            self.assertEqual(order_phase("MYSTERY"), WORKING)
        self.assertEqual(len(logs.records), 1)


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
