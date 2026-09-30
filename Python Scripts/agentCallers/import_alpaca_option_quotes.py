from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.settings import load_process_settings

if __name__ == "__main__":
    # Before importing modules that read configuration at import time.
    load_process_settings()

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
