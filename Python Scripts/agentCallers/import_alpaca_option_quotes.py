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

from db_helpers.market import import_alpaca_option_quote_csv  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import an Alpaca historical option bid/ask CSV for read-only backtests."
    )
    parser.add_argument("csv_path", type=Path, help="CSV export containing option symbol, timestamp, bid, and ask columns.")
    parser.add_argument("--provider", default="alpaca_csv", help="Source label persisted with the quotes.")
    parser.add_argument("--batch-size", type=int, default=1_000, help="Rows per SQLite insert batch.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report CSV coverage without writing quotes.")
    args = parser.parse_args()
    result = import_alpaca_option_quote_csv(
        args.csv_path,
        provider=args.provider,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
