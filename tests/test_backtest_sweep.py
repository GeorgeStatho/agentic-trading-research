from __future__ import annotations

from typing import Any, Callable
import unittest

from backtest_sweep import SweepWindows, parse_setting, run_sweep


def _fake_evaluate(calls: list[tuple]) -> Callable[..., dict[str, Any]]:
    def evaluate(config: dict[str, float], start_at: str | None, end_at: str | None) -> dict[str, Any]:
        calls.append((dict(config), start_at, end_at))
        take_profit = config.get("take_profit_pct", 0.0)
        return {"total_pnl_dollars": take_profit, "filled_count": int(take_profit)}
    return evaluate


class SweepWindowsTests(unittest.TestCase):
    def test_tuning_ends_before_the_validation_window_starts(self) -> None:
        windows = SweepWindows(split_at="2026-09-01T00:00:00+00:00")
        self.assertEqual(windows.tuning(), (None, "2026-08-31T23:59:59.999999+00:00"))


class ParseSettingTests(unittest.TestCase):
    def test_production_trailing_settings_can_be_swept(self) -> None:
        for name in ("trailing_trigger_pct", "trailing_floor_pct", "trailing_giveback_pct"):
            self.assertEqual(parse_setting(f"{name}=20,30"), (name, [20.0, 30.0]))


class RunSweepTests(unittest.TestCase):
    def _sweep(self, calls: list[tuple], *, split_at: str | None = "2026-09-01T00:00:00+00:00"):
        return run_sweep(
            {"take_profit_pct": [10.0, 50.0, 100.0]},
            evaluate=_fake_evaluate(calls),
            windows=SweepWindows(split_at=split_at),
            rank_by="total_pnl_dollars",
            min_fills=20,
            top=1,
        )

    def test_ranks_best_first_and_leaves_out_thin_samples(self) -> None:
        _, _, best = self._sweep([])
        self.assertEqual([row.config for row in best], [{"take_profit_pct": 100.0}])

    def test_validates_only_the_baseline_and_the_top_rows_on_the_later_window(self) -> None:
        calls: list[tuple] = []
        self._sweep(calls)
        validation_calls = [call for call in calls if call[1] == "2026-09-01T00:00:00+00:00"]
        self.assertEqual([call[0] for call in validation_calls], [{}, {"take_profit_pct": 100.0}])


if __name__ == "__main__":
    unittest.main()
