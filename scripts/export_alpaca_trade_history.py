#!/usr/bin/env python3
"""Export the account's option trade history straight from Alpaca.

Reads PUBLIC_KEY / PRIVATE_KEY / ALPACA_PAPER from the environment (or a .env
file next to the repo root), pulls account activities (fills, option
expirations, cash transfers), reconstructs per-contract round trips, and
prints summary statistics. Optionally writes the round trips to CSV.

Run on any machine with valid keys (stdlib only, no extra deps):

    python scripts/export_alpaca_trade_history.py --days 120
    python scripts/export_alpaca_trade_history.py --days 180 --csv Data/alpaca_trade_history.csv

or inside the deployed stack, which already has working keys:

    docker compose exec worker python scripts/export_alpaca_trade_history.py --days 120
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{1,8}\d{6}[CP]\d{8}$")
CONTRACT_MULTIPLIER = 100


def load_env_file() -> None:
    """Populate os.environ from a repo-root .env for keys not already set."""
    for candidate in (Path(__file__).resolve().parents[1] / ".env", Path(".env")):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return


def api_get(base_url: str, path: str, params: dict | None = None) -> object:
    url = base_url + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(
        url,
        headers={
            "APCA-API-KEY-ID": os.environ["PUBLIC_KEY"],
            "APCA-API-SECRET-KEY": os.environ["PRIVATE_KEY"],
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def fetch_activities(base_url: str, after_iso: str) -> list[dict]:
    activities: list[dict] = []
    page_token: str | None = None
    while True:
        params = {"after": after_iso, "direction": "asc", "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        batch = api_get(base_url, "/v2/account/activities", params)
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < 100:
            break
        page_token = str(batch[-1].get("id") or "")
        if not page_token:
            break
    return activities


def parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def reconstruct_round_trips(activities: list[dict]) -> tuple[list[dict], list[dict]]:
    """FIFO-match option buys against sells/expirations per contract symbol."""
    fills_by_symbol: dict[str, list[dict]] = defaultdict(list)
    transfers: list[dict] = []

    for activity in activities:
        activity_type = str(activity.get("activity_type") or "")
        if activity_type in {"CSD", "CSW", "TRANS"}:
            transfers.append(activity)
            continue
        symbol = str(activity.get("symbol") or "").strip().upper()
        if not OPTION_SYMBOL_PATTERN.match(symbol):
            continue
        if activity_type == "FILL":
            fills_by_symbol[symbol].append(
                {
                    "time": parse_time(activity.get("transaction_time")),
                    "side": str(activity.get("side") or "").lower(),
                    "qty": abs(float(activity.get("qty") or 0)),
                    "price": float(activity.get("price") or 0),
                }
            )
        elif activity_type == "OPEXP":
            fills_by_symbol[symbol].append(
                {
                    "time": parse_time(activity.get("date")) or parse_time(activity.get("transaction_time")),
                    "side": "expire",
                    "qty": abs(float(activity.get("qty") or 0)),
                    "price": 0.0,
                }
            )

    round_trips: list[dict] = []
    for symbol, fills in fills_by_symbol.items():
        fills.sort(key=lambda fill: fill["time"] or datetime.min.replace(tzinfo=timezone.utc))
        open_lots: list[dict] = []
        current: dict | None = None
        for fill in fills:
            if fill["side"] == "buy":
                if current is None:
                    current = {
                        "symbol": symbol,
                        "underlying": re.match(r"^([A-Z0-9]{1,8})\d{6}[CP]", symbol).group(1),
                        "opened_at": fill["time"],
                        "closed_at": None,
                        "qty": 0.0,
                        "cost": 0.0,
                        "proceeds": 0.0,
                        "closed_qty": 0.0,
                        "expired_qty": 0.0,
                    }
                current["qty"] += fill["qty"]
                current["cost"] += fill["qty"] * fill["price"] * CONTRACT_MULTIPLIER
                open_lots.append(fill)
            elif fill["side"] in {"sell", "expire"} and current is not None:
                closing_qty = min(fill["qty"], current["qty"] - current["closed_qty"])
                current["proceeds"] += closing_qty * fill["price"] * CONTRACT_MULTIPLIER
                current["closed_qty"] += closing_qty
                if fill["side"] == "expire":
                    current["expired_qty"] += closing_qty
                current["closed_at"] = fill["time"]
                if current["closed_qty"] >= current["qty"] - 1e-9:
                    round_trips.append(current)
                    current = None
        if current is not None:
            round_trips.append({**current, "open": True})

    round_trips.sort(key=lambda trip: trip["opened_at"] or datetime.min.replace(tzinfo=timezone.utc))
    return round_trips, transfers


def summarize(round_trips: list[dict], transfers: list[dict]) -> None:
    closed = [trip for trip in round_trips if not trip.get("open")]
    print(f"\n{'symbol':<24} {'opened':<11} {'closed':<11} {'days':>4} {'qty':>3} "
          f"{'cost $':>8} {'proceeds $':>10} {'P/L $':>8} {'P/L %':>7}  note")
    for trip in round_trips:
        pnl = trip["proceeds"] - trip["cost"]
        pnl_pct = 100.0 * pnl / trip["cost"] if trip["cost"] else 0.0
        opened = trip["opened_at"].date().isoformat() if trip["opened_at"] else "?"
        closed_at = trip["closed_at"].date().isoformat() if trip["closed_at"] else "OPEN"
        held_days = (
            (trip["closed_at"] - trip["opened_at"]).days
            if trip["closed_at"] and trip["opened_at"]
            else 0
        )
        note = "expired" if trip["expired_qty"] else ("open" if trip.get("open") else "")
        print(f"{trip['symbol']:<24} {opened:<11} {closed_at:<11} {held_days:>4} {trip['qty']:>3.0f} "
              f"{trip['cost']:>8.0f} {trip['proceeds']:>10.0f} {pnl:>8.0f} {pnl_pct:>6.1f}%  {note}")

    if closed:
        pnls = [trip["proceeds"] - trip["cost"] for trip in closed]
        pnl_pcts = [100.0 * (trip["proceeds"] - trip["cost"]) / trip["cost"] for trip in closed if trip["cost"]]
        wins = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value <= 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        print(f"\nclosed round trips: {len(closed)} | win rate: {100.0 * len(wins) / len(closed):.0f}%")
        print(f"total P/L: ${sum(pnls):.0f} | avg per trade: ${sum(pnls) / len(closed):.0f} "
              f"({sum(pnl_pcts) / len(pnl_pcts):.1f}% of premium)")
        if wins:
            print(f"avg win: ${gross_win / len(wins):.0f} | ", end="")
        if losses:
            print(f"avg loss: ${gross_loss / len(losses):.0f} | ", end="")
        if gross_loss > 0:
            print(f"profit factor: {gross_win / gross_loss:.2f}")
        else:
            print("profit factor: inf")

        by_month: dict[str, float] = defaultdict(float)
        by_underlying: dict[str, float] = defaultdict(float)
        for trip in closed:
            month = trip["closed_at"].strftime("%Y-%m") if trip["closed_at"] else "?"
            by_month[month] += trip["proceeds"] - trip["cost"]
            by_underlying[trip["underlying"]] += trip["proceeds"] - trip["cost"]
        print("P/L by month closed:", {key: round(value) for key, value in sorted(by_month.items())})
        print("P/L by underlying:", {key: round(value) for key, value in
                                     sorted(by_underlying.items(), key=lambda item: item[1])})

    if transfers:
        deposits = sum(float(t.get("net_amount") or 0) for t in transfers
                       if float(t.get("net_amount") or 0) > 0)
        withdrawals = -sum(float(t.get("net_amount") or 0) for t in transfers
                           if float(t.get("net_amount") or 0) < 0)
        print(f"\ncash transfers in window: deposits ${deposits:.0f}, withdrawals ${withdrawals:.0f}")


def write_csv(round_trips: list[dict], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "underlying", "opened_at", "closed_at", "qty",
                         "cost", "proceeds", "pnl", "pnl_pct", "expired_qty", "still_open"])
        for trip in round_trips:
            pnl = trip["proceeds"] - trip["cost"]
            writer.writerow([
                trip["symbol"], trip["underlying"],
                trip["opened_at"].isoformat() if trip["opened_at"] else "",
                trip["closed_at"].isoformat() if trip["closed_at"] else "",
                trip["qty"], round(trip["cost"], 2), round(trip["proceeds"], 2),
                round(pnl, 2),
                round(100.0 * pnl / trip["cost"], 2) if trip["cost"] else "",
                trip["expired_qty"], bool(trip.get("open")),
            ])
    print(f"\nwrote {csv_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=120, help="How many days back to fetch (default 120).")
    parser.add_argument("--csv", type=str, default="", help="Optional CSV output path for round trips.")
    parser.add_argument("--paper", action="store_true", help="Force the paper endpoint.")
    parser.add_argument("--live", action="store_true", help="Force the live endpoint.")
    args = parser.parse_args()

    load_env_file()
    if not os.environ.get("PUBLIC_KEY") or not os.environ.get("PRIVATE_KEY"):
        print("PUBLIC_KEY / PRIVATE_KEY not found in environment or .env", file=sys.stderr)
        return 1

    paper_flag = str(os.environ.get("ALPACA_PAPER", "true")).strip().lower() not in {"0", "false", "no", "off"}
    if args.paper:
        paper_flag = True
    if args.live:
        paper_flag = False
    base_url = "https://paper-api.alpaca.markets" if paper_flag else "https://api.alpaca.markets"

    account = api_get(base_url, "/v2/account")
    print(f"endpoint: {'paper' if paper_flag else 'LIVE'} | account status: {account.get('status')} "
          f"| equity: ${float(account.get('equity') or 0):.2f}")

    after_iso = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    activities = fetch_activities(base_url, after_iso)
    print(f"activities since {after_iso[:10]}: {len(activities)}")

    round_trips, transfers = reconstruct_round_trips(activities)
    if not round_trips:
        print("no option round trips found in this window")
        return 0
    summarize(round_trips, transfers)
    if args.csv:
        write_csv(round_trips, Path(args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
