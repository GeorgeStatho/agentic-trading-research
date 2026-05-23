from __future__ import annotations

import argparse

from . import _run_reference_price_smoke_test


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Smoke-test the Alpaca-backed stock reference price calculation."
    )
    parser.add_argument("symbol", nargs="?", help="Ticker symbol to inspect, e.g. SHOP")
    args = parser.parse_args()
    raise SystemExit(_run_reference_price_smoke_test(args.symbol or "AAPL"))
