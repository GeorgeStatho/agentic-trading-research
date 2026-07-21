from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parent
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers, load_project_env

bootstrap_agent_callers()
load_project_env()

from db_helpers.market import run_backtest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic, read-only historical option backtest.")
    parser.add_argument("--start-at", default="", help="Inclusive ISO-8601 decision timestamp.")
    parser.add_argument("--end-at", default="", help="Inclusive ISO-8601 decision timestamp.")
    parser.add_argument("--limit", type=int, default=250, help="Maximum stored manager decisions to simulate.")
    parser.add_argument("--entry-slippage-bps", type=float, default=25.0)
    parser.add_argument("--exit-slippage-bps", type=float, default=25.0)
    parser.add_argument("--commission-per-contract", type=float, default=0.65)
    parser.add_argument("--max-hold-hours", type=int, default=168)
    args = parser.parse_args()
    result = run_backtest(
        config={
            "entry_slippage_bps": args.entry_slippage_bps,
            "exit_slippage_bps": args.exit_slippage_bps,
            "commission_per_contract": args.commission_per_contract,
            "max_hold_hours": args.max_hold_hours,
        },
        start_at=args.start_at or None,
        end_at=args.end_at or None,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
