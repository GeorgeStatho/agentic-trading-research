from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from portfolio_history_service import DEFAULT_OUTPUT_PATH, fetch_portfolio_history, load_env, write_output


def main() -> int:
    load_env()
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
