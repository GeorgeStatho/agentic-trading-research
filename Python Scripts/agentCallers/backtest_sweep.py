"""Grid-search the backtest's execution and exit settings over the stored manager decisions.

Decisions are frozen, so only fills, exits and portfolio limits are tuned. Configurations are
ranked on the tuning window and the best are re-run on the later validation window. No backtest
runs are recorded and no broker is called; the first pass caches decision evidence snapshots,
as any backtest does.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import itertools
from pathlib import Path
import sys
from typing import Any, Callable

from services.core.settings import load_process_settings

if __name__ == "__main__":
    # Before importing modules that read configuration at import time.
    load_process_settings()

from db_common import database_path  # noqa: E402
from market_db_modules.backtesting import simulate_backtest_metrics  # noqa: E402


SWEEPABLE_SETTINGS = (
    "take_profit_pct",
    "stop_loss_pct",
    "max_hold_hours",
    "max_entry_delay_minutes",
    "max_spread_pct",
    "min_volume",
    "max_concurrent_positions",
    "max_total_entry_notional",
    "max_sector_notional_pct",
    "daily_loss_limit_dollars",
    "entry_slippage_bps",
    "exit_slippage_bps",
    "commission_per_contract",
)
DEFAULT_GRID: dict[str, list[float]] = {
    "take_profit_pct": [25.0, 50.0, 75.0, 100.0],
    "stop_loss_pct": [-25.0, -40.0, -50.0, -70.0],
    # Whole days, because the stored option quotes are end-of-day closes.
    "max_hold_hours": [48.0, 120.0, 168.0, 336.0],
}
RANK_METRICS = ("total_pnl_dollars", "expectancy_pct", "median_pnl_pct", "profit_factor_like", "win_rate_pct")
REPORTED_METRICS = (
    "filled_count",
    "total_pnl_dollars",
    "expectancy_pct",
    "median_pnl_pct",
    "win_rate_pct",
    "profit_factor_like",
    "max_drawdown_dollars",
    "portfolio_rejected_count",
    "no_fill_count",
    "no_exit_quote_count",
)
BASELINE_LABEL = "defaults"

Evaluate = Callable[[dict[str, float], str | None, str | None], dict[str, Any]]


@dataclass(frozen=True)
class SweepWindows:
    """Tuning runs up to just before split_at; validation runs from split_at to end_at."""

    start_at: str | None = None
    split_at: str | None = None
    end_at: str | None = None

    def tuning(self) -> tuple[str | None, str | None]:
        if self.split_at is None:
            return self.start_at, self.end_at
        # The engine's end bound is inclusive, so stop one tick early to keep the windows disjoint.
        return self.start_at, (_parse_timestamp(self.split_at) - timedelta(microseconds=1)).isoformat()

    def validation(self) -> tuple[str | None, str | None] | None:
        if self.split_at is None:
            return None
        return self.split_at, self.end_at


@dataclass
class SweepRow:
    label: str
    config: dict[str, float]
    tuning: dict[str, Any]
    validation: dict[str, Any] | None = None


def _parse_timestamp(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def parse_setting(text: str) -> tuple[str, list[float]]:
    name, separator, raw_values = text.partition("=")
    name = name.strip()
    if not separator or name not in SWEEPABLE_SETTINGS:
        raise argparse.ArgumentTypeError(f"expected NAME=V1,V2,... where NAME is one of: {', '.join(SWEEPABLE_SETTINGS)}")
    try:
        values = [float(value) for value in raw_values.split(",") if value.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name}: values must be numbers") from exc
    if not values:
        raise argparse.ArgumentTypeError(f"{name}: give at least one value")
    return name, values


def expand_grid(grid: dict[str, list[float]]) -> list[dict[str, float]]:
    names = list(grid)
    return [dict(zip(names, combination)) for combination in itertools.product(*grid.values())]


def describe_config(config: dict[str, float]) -> str:
    return " ".join(f"{name}={value:g}" for name, value in config.items()) or BASELINE_LABEL


def _reported(metrics: dict[str, Any]) -> dict[str, Any]:
    return {name: metrics.get(name) for name in REPORTED_METRICS}


def rank_rows(rows: list[SweepRow], *, rank_by: str, min_fills: int) -> list[SweepRow]:
    """Best first, leaving out configurations with too few fills to judge."""
    eligible = [
        row for row in rows
        if (row.tuning.get("filled_count") or 0) >= min_fills and row.tuning.get(rank_by) is not None
    ]
    return sorted(eligible, key=lambda row: row.tuning[rank_by], reverse=True)


def run_sweep(
    grid: dict[str, list[float]],
    *,
    evaluate: Evaluate,
    windows: SweepWindows,
    rank_by: str,
    min_fills: int,
    top: int,
    on_progress: Callable[[int, int], None] = lambda done, total: None,
) -> tuple[SweepRow, list[SweepRow], list[SweepRow]]:
    """Return the baseline, every grid row, and the validated top rows."""
    configs = expand_grid(grid)
    tuning_start, tuning_end = windows.tuning()
    baseline = SweepRow(BASELINE_LABEL, {}, _reported(evaluate({}, tuning_start, tuning_end)))
    rows = []
    for index, config in enumerate(configs, start=1):
        rows.append(SweepRow(describe_config(config), config, _reported(evaluate(config, tuning_start, tuning_end))))
        on_progress(index, len(configs))
    best = rank_rows(rows, rank_by=rank_by, min_fills=min_fills)[:top]
    validation_window = windows.validation()
    if validation_window is not None:
        for row in [baseline, *best]:
            row.validation = _reported(evaluate(row.config, *validation_window))
    return baseline, rows, best


def write_csv(path: Path, baseline: SweepRow, rows: list[SweepRow], setting_names: list[str]) -> None:
    columns = [
        "label",
        *setting_names,
        *(f"tune_{name}" for name in REPORTED_METRICS),
        *(f"validation_{name}" for name in REPORTED_METRICS),
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in [baseline, *rows]:
            writer.writerow({
                "label": row.label,
                **row.config,
                **{f"tune_{name}": value for name, value in row.tuning.items()},
                **{f"validation_{name}": value for name, value in (row.validation or {}).items()},
            })


def _format_metric(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}" if isinstance(value, float) else str(value)


def print_report(baseline: SweepRow, best: list[SweepRow], *, rank_by: str, validated: bool) -> None:
    header = f"{'rank':<9}{'tune ' + rank_by:>24}{'fills':>7}"
    if validated:
        header += f"{'valid ' + rank_by:>25}{'fills':>7}"
    print(header + "  settings")
    for rank, row in [(BASELINE_LABEL, baseline), *enumerate(best, start=1)]:
        line = f"{rank!s:<9}{_format_metric(row.tuning[rank_by]):>24}{_format_metric(row.tuning['filled_count']):>7}"
        if validated:
            validation = row.validation or {}
            line += f"{_format_metric(validation.get(rank_by)):>25}{_format_metric(validation.get('filled_count')):>7}"
        print(f"{line}  {row.label}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grid-search backtest exit and execution settings.")
    parser.add_argument("--db", type=Path, default=None, help="SQLite database; defaults to the current database (the DB_PATH env value).")
    parser.add_argument(
        "--setting", action="append", type=parse_setting, metavar="NAME=V1,V2,...",
        help=f"A setting and the values to try; repeat for each. Default grid: {DEFAULT_GRID}.",
    )
    parser.add_argument("--start-at", help="Inclusive ISO-8601 start of the tuning window.")
    parser.add_argument("--split-at", help="ISO-8601 start of the validation window; omit to skip validation.")
    parser.add_argument("--end-at", help="Inclusive ISO-8601 end of the last window.")
    parser.add_argument("--rank-by", choices=RANK_METRICS, default="total_pnl_dollars")
    parser.add_argument("--min-fills", type=int, default=30, help="Leave out configurations with fewer tuning fills.")
    parser.add_argument("--top", type=int, default=10, help="How many of the best configurations to validate and print.")
    parser.add_argument("--csv", type=Path, help="Write every configuration's metrics to this CSV file.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    args.db = Path(database_path(args.db))
    if not args.db.is_file():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 2
    grid = dict(args.setting) if args.setting else DEFAULT_GRID

    def evaluate(config: dict[str, float], start_at: str | None, end_at: str | None) -> dict[str, Any]:
        return simulate_backtest_metrics(config=config, start_at=start_at, end_at=end_at, db_path=args.db)

    def report_progress(done: int, total: int) -> None:
        print(f"\rsimulated {done}/{total} configurations", end="" if done < total else "\n", file=sys.stderr)

    windows = SweepWindows(start_at=args.start_at, split_at=args.split_at, end_at=args.end_at)
    baseline, rows, best = run_sweep(
        grid,
        evaluate=evaluate,
        windows=windows,
        rank_by=args.rank_by,
        min_fills=args.min_fills,
        top=args.top,
        on_progress=report_progress,
    )
    print_report(baseline, best, rank_by=args.rank_by, validated=windows.validation() is not None)
    if args.csv:
        write_csv(args.csv, baseline, rows, list(grid))
        print(f"\nWrote {len(rows) + 1} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
