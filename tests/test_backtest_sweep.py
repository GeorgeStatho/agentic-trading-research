from __future__ import annotations

import argparse
from typing import Any, Callable
import unittest

from backtest_sweep import BASELINE_LABEL, SweepWindows, expand_grid, parse_setting, run_sweep


def _fake_evaluate(calls: list[tuple]) -> Callable[..., dict[str, Any]]:
    def evaluate(config: dict[str, float], start_at: str | None, end_at: str | None) -> dict[str, Any]:
        calls.append((dict(config), start_at, end_at))
        take_profit = config.get("take_profit_pct", 0.0)
        return {"total_pnl_dollars": take_profit, "filled_count": int(take_profit)}
    return evaluate


class ParseSettingTests(unittest.TestCase):
    def test_reads_name_and_numeric_values(self) -> None:
        self.assertEqual(parse_setting("stop_loss_pct=-25,-50"), ("stop_loss_pct", [-25.0, -50.0]))

    def test_rejects_settings_the_engine_does_not_sweep(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_setting("option_multiplier=10")

    def test_rejects_non_numeric_values(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_setting("take_profit_pct=high")


class ExpandGridTests(unittest.TestCase):
    def test_builds_every_combination(self) -> None:
        grid = {"take_profit_pct": [25.0, 50.0], "stop_loss_pct": [-25.0, -50.0, -70.0]}
        self.assertEqual(len(expand_grid(grid)), 6)


class SweepWindowsTests(unittest.TestCase):
    def test_tuning_ends_before_the_validation_window_starts(self) -> None:
        windows = SweepWindows(split_at="2026-09-01T00:00:00+00:00")
        self.assertEqual(windows.tuning(), (None, "2026-08-31T23:59:59.999999+00:00"))

    def test_without_a_split_there_is_no_validation_window(self) -> None:
        self.assertIsNone(SweepWindows(end_at="2026-09-30").validation())


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

    def test_baseline_runs_the_engine_defaults(self) -> None:
        baseline, _, _ = self._sweep([])
        self.assertEqual((baseline.label, baseline.config), (BASELINE_LABEL, {}))

    def test_skips_validation_without_a_split(self) -> None:
        baseline, _, best = self._sweep([], split_at=None)
        self.assertIsNone(best[0].validation)


if __name__ == "__main__":
    unittest.main()
