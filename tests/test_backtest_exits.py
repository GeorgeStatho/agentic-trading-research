"""Production's exit rules walked over backtest marks."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests.test_llm_backtest import PRODUCTION_EXITS
from trading_support.backtest_exits import production_exit

START = datetime(2026, 3, 3, 15, tzinfo=timezone.utc)
EXPIRATION = datetime(2026, 4, 17, 20, tzinfo=timezone.utc)


def marks(*points: tuple[float, float]) -> list[tuple[datetime, float, float, float]]:
    """(mid, spot) an hour apart; the exit bid is 2% under the mid."""
    return [(START + timedelta(hours=index), mid, mid * 0.98, spot) for index, (mid, spot) in enumerate(points)]


def walk(path, *, quantity=1, side="call", **levels):
    return production_exit(path, entry_price=10.0, quantity=quantity, side=side, entry_spot=400.0,
                           expiration=EXPIRATION, entry_dte=46, settings=PRODUCTION_EXITS, **levels)


class ProductionExitTests(unittest.TestCase):
    def test_scales_out_half_then_exits_the_rest_on_the_trailing_floor(self) -> None:
        fills, reasons = walk(marks((16.0, 410.0), (12.0, 405.0)), quantity=2)
        self.assertEqual(reasons, ["first_scale_out_trigger", "trailing_profit_stop"])
        self.assertEqual([(qty, round(price, 2)) for _, qty, price in fills], [(1, 15.68), (1, 11.76)])

    def test_premium_stop_waits_for_the_stock_to_move_against_the_position(self) -> None:
        # Premium down 45% while the stock is off under 1.5%: production holds through the noise.
        fills, reasons = walk(marks((5.5, 397.0), (5.5, 390.0)))
        self.assertEqual(reasons, ["stop_loss"])
        self.assertEqual(fills[0][0], START + timedelta(hours=1))

    def test_unknown_stock_price_falls_back_to_the_premium_stop(self) -> None:
        path = [(START, 5.5, 5.4, None)]
        self.assertEqual(walk(path)[1], ["stop_loss"])

    def test_put_reads_a_rising_stock_as_the_adverse_move(self) -> None:
        self.assertEqual(walk(marks((5.5, 410.0)), side="put")[1], ["stop_loss"])

    def test_a_given_stop_loss_replaces_the_dte_rule(self) -> None:
        self.assertEqual(walk(marks((5.5, 390.0)), stop_loss_pct=-60.0), ([], []))

    def test_holds_when_no_rule_fires(self) -> None:
        self.assertEqual(walk(marks((10.5, 401.0), (9.5, 399.0))), ([], []))


if __name__ == "__main__":
    unittest.main()
