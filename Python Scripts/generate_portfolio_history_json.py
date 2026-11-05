from __future__ import annotations

import sys

from portfolio_history_service import DEFAULT_OUTPUT_PATH, fetch_portfolio_history, write_output
from services.core.settings import load_process_settings


def main() -> int:
    load_process_settings()
    portfolio_history = fetch_portfolio_history()
    write_output(portfolio_history, DEFAULT_OUTPUT_PATH)
    print(f"Wrote portfolio history to {DEFAULT_OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
