from __future__ import annotations

import argparse
import json

from services.settings import load_process_settings

if __name__ == "__main__":
    # Before importing modules that read configuration at import time.
    load_process_settings()

from market_db_modules.backtest_marketdata import fetch_marketdata_eod_option_quotes  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch MarketData.app historical end-of-day option bid/ask quotes for stored decisions; never places orders."
    )
    parser.add_argument("--start-at", default="", help="Inclusive ISO-8601 manager-decision cutoff.")
    parser.add_argument("--end-at", default="", help="Inclusive ISO-8601 manager-decision cutoff.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum uncached contract windows to attempt per batch; repeated runs advance through history.")
    parser.add_argument("--entry-delay-minutes", type=int, default=1_440)
    parser.add_argument("--max-hold-hours", type=int, default=168)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--force", action="store_true", help="Refetch completed cached windows as well as missing windows.")
    args = parser.parse_args()
    result = fetch_marketdata_eod_option_quotes(
        start_at=args.start_at or None,
        end_at=args.end_at or None,
        limit=args.limit,
        entry_delay_minutes=args.entry_delay_minutes,
        max_hold_hours=args.max_hold_hours,
        timeout_seconds=args.timeout_seconds,
        force=args.force,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
