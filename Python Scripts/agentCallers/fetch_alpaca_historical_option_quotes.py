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

from db_helpers.market import fetch_alpaca_historical_option_quotes  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch historical Alpaca option bid/ask quotes for stored manager decisions; never places orders."
    )
    parser.add_argument("--start-at", default="", help="Inclusive ISO-8601 manager-decision cutoff.")
    parser.add_argument("--end-at", default="", help="Inclusive ISO-8601 manager-decision cutoff.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum stored manager decisions to inspect.")
    parser.add_argument("--feed", choices=("indicative", "opra"), default=None, help="Alpaca option data feed.")
    parser.add_argument("--entry-delay-minutes", type=int, default=1_440)
    parser.add_argument("--max-hold-hours", type=int, default=168)
    parser.add_argument("--page-limit", type=int, default=10_000)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--force", action="store_true", help="Fetch even when two valid quotes are already cached for a decision window.")
    args = parser.parse_args()
    result = fetch_alpaca_historical_option_quotes(
        start_at=args.start_at or None,
        end_at=args.end_at or None,
        limit=args.limit,
        feed=args.feed,
        entry_delay_minutes=args.entry_delay_minutes,
        max_hold_hours=args.max_hold_hours,
        page_limit=args.page_limit,
        timeout_seconds=args.timeout_seconds,
        force=args.force,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
