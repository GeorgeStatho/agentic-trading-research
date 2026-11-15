"""Alpaca 1-minute stock and option bars for backtests, fetched once per completed session.

Reads market data only: no brokerage or order endpoint is called. Stock bars are SIP bars (free once
15 minutes old) stored in historical_underlying_bars as timeframe '1m'; option bars are trade bars stored in
historical_option_bars. Every requested session gets a historical_bar_fetches row, even with no bars, so a
contract that never traded is not fetched again.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db_common import get_connection
from services.core.time_utils import parse_timestamp
from .backtest_imports import _normalized_option_symbol
from .backtest_marketdata import _decision_rows
from .backtesting import OCC_SYMBOL
from services.options.option_pricing import expiration_close
from .bootstrap import initialize_database

STOCK_PROVIDER = "alpaca_sip"
OPTION_PROVIDER = "alpaca_opra"
TIMEFRAME = "1m"
MARKET_TIMEZONE = ZoneInfo("America/New_York")
SESSION_OPEN, SESSION_CLOSE = time(9, 30), time(16)
# SIP bars are free once they are 15 minutes old.
SETTLE_DELAY = timedelta(minutes=15)


def record_option_bars(option_symbol: str, bars: list[dict[str, Any]], *, provider: str = OPTION_PROVIDER,
                       db_path: Path | str | None = None) -> int:
    """Store option bars given in Alpaca's REST shape (t, o, h, l, c, v, n, vw); returns rows inserted."""
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        return _insert_option_bars(conn, option_symbol, bars, provider)


def fetch_alpaca_minute_bars(
    *,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int = 10,
    max_hold_hours: int = 168,
    stock_client: Any = None,
    option_client: Any = None,
    now: datetime | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Fetch the minute bars that stored call/put decisions' hold windows still lack, oldest decision first.

    ``limit`` bounds the decisions that need a request in one call; fully cached decisions don't count.
    A failed request is counted and its sessions stay unfetched, so the next call retries them.
    """
    if limit < 1 or max_hold_hours < 1:
        raise ValueError("limit and max_hold_hours must be at least 1.")
    now = now or datetime.now(timezone.utc)
    initialize_database(db_path=db_path)
    result: dict[str, Any] = {"read_only_trading": True, "stock_provider": STOCK_PROVIDER,
                              "option_provider": OPTION_PROVIDER, "eligible_decision_count": 0,
                              "fetched_decision_count": 0, "already_cached_count": 0, "remaining_decision_count": 0,
                              "stock_bar_count": 0, "option_bar_count": 0, "error_count": 0, "errors": []}
    with closing(get_connection(db_path)) as conn:
        decisions = _decision_rows(conn, start_at=start_at, end_at=end_at, limit=None)
    decisions.sort(key=lambda row: (row["decision_run_at"], row["id"]))
    result["eligible_decision_count"] = len(decisions)
    clients = {"stock": stock_client, "option": option_client}
    for decision in decisions:
        targets = _targets(decision, max_hold_hours=max_hold_hours, now=now)
        with closing(get_connection(db_path)) as conn:
            pending = [(kind, symbol, missing) for kind, symbol, sessions in targets
                       if (missing := _unfetched(conn, symbol, sessions, _provider(kind)))]
        if not pending:
            result["already_cached_count"] += bool(targets)
            continue
        if result["fetched_decision_count"] >= limit:
            result["remaining_decision_count"] += 1
            continue
        result["fetched_decision_count"] += 1
        for kind, symbol, sessions in pending:
            if clients[kind] is None:
                clients[kind] = _default_client(kind)
            try:
                bars = _request_bars(clients[kind], kind, symbol, sessions)
            except Exception as exc:  # One bad window must not stop the batch; its sessions retry next call.
                result["error_count"] += 1
                result["errors"] = (result["errors"] + [{"symbol": symbol, "error": str(exc)[:200]}])[:10]
                continue
            with closing(get_connection(db_path)) as conn, conn:
                inserted = (_insert_stock_bars(conn, symbol, bars) if kind == "stock"
                            else _insert_option_bars(conn, symbol, bars, OPTION_PROVIDER))
                _record_fetches(conn, symbol, sessions, bars, _provider(kind), now)
            result["stock_bar_count" if kind == "stock" else "option_bar_count"] += inserted
    return result


def _provider(kind: str) -> str:
    return STOCK_PROVIDER if kind == "stock" else OPTION_PROVIDER


def _targets(decision: dict[str, Any], *, max_hold_hours: int, now: datetime) -> list[tuple[str, str, list[date]]]:
    """(kind, symbol, completed sessions) for the decision's stock and contract over its hold window."""
    decided_at = parse_timestamp(decision.get("decision_run_at"))
    option_symbol = _normalized_option_symbol(decision.get("selected_option_symbol"))
    symbol = str(decision.get("symbol") or "").strip().upper()
    if decided_at is None or not option_symbol or not symbol:
        return []
    until = decided_at + timedelta(hours=max_hold_hours)
    match = OCC_SYMBOL.match(option_symbol)
    expiry = expiration_close("20{}-{}-{}".format(*match.group(2, 3, 4))) if match else None
    if expiry is not None:
        until = min(until, expiry)
    day, last = decided_at.astimezone(MARKET_TIMEZONE).date(), until.astimezone(MARKET_TIMEZONE).date()
    sessions = []
    while day <= last:
        # shortcut: weekends are skipped but holidays are requested once and stored as 0-bar sessions.
        if day.weekday() < 5 and datetime.combine(day, SESSION_CLOSE, MARKET_TIMEZONE) + SETTLE_DELAY <= now:
            sessions.append(day)
        day += timedelta(days=1)
    return [("stock", symbol, sessions), ("option", option_symbol, sessions)] if sessions else []


def _unfetched(conn, symbol: str, sessions: list[date], provider: str) -> list[date]:
    done = {row[0] for row in conn.execute(
        "SELECT session_date FROM historical_bar_fetches WHERE symbol=? AND timeframe=? AND provider=?",
        (symbol, TIMEFRAME, provider))}
    return [day for day in sessions if day.isoformat() not in done]


def _default_client(kind: str) -> Any:
    from trading_support.clients import get_option_history_client, get_stock_history_client
    return get_stock_history_client() if kind == "stock" else get_option_history_client()


def _request_bars(client: Any, kind: str, symbol: str, sessions: list[date]) -> list[dict[str, Any]]:
    """Regular-hours bars from the first session's open to the last one's close, in Alpaca's REST shape."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import OptionBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    start = datetime.combine(sessions[0], SESSION_OPEN, MARKET_TIMEZONE).astimezone(timezone.utc)
    end = datetime.combine(sessions[-1], SESSION_CLOSE, MARKET_TIMEZONE).astimezone(timezone.utc)
    if kind == "stock":
        response = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                                                          start=start, end=end, feed=DataFeed.SIP))
    else:
        response = client.get_option_bars(OptionBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                                                            start=start, end=end))
    wanted = set(sessions)
    bars = []
    for item in (getattr(response, "data", None) or {}).get(symbol, []):
        at = item.timestamp.astimezone(MARKET_TIMEZONE)
        if at.date() in wanted and SESSION_OPEN <= at.time() < SESSION_CLOSE:
            bars.append({"t": item.timestamp.astimezone(timezone.utc).isoformat(), "o": item.open, "h": item.high,
                         "l": item.low, "c": item.close, "v": item.volume, "n": item.trade_count, "vw": item.vwap})
    return bars


def _insert_stock_bars(conn, symbol: str, bars: list[dict[str, Any]]) -> int:
    before = conn.total_changes
    conn.executemany(
        """INSERT INTO historical_underlying_bars (symbol, bar_at, timeframe, open_price, high_price, low_price,
               close_price, volume, provider) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol, bar_at, timeframe, provider) DO NOTHING""",
        [(symbol, bar["t"], TIMEFRAME, bar["o"], bar["h"], bar["l"], bar["c"], bar["v"], STOCK_PROVIDER)
         for bar in bars])
    return conn.total_changes - before


def _insert_option_bars(conn, option_symbol: str, bars: list[dict[str, Any]], provider: str) -> int:
    before = conn.total_changes
    conn.executemany(
        """INSERT INTO historical_option_bars (option_symbol, bar_at, timeframe, open_price, high_price, low_price,
               close_price, volume, vwap, trade_count, provider) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(option_symbol, bar_at, timeframe, provider) DO NOTHING""",
        [(option_symbol.upper(), parse_timestamp(bar["t"]).astimezone(timezone.utc).isoformat(), TIMEFRAME,
          bar.get("o"), bar.get("h"), bar.get("l"), bar["c"], bar.get("v"), bar.get("vw"), bar.get("n"), provider)
         for bar in bars])
    return conn.total_changes - before


def _record_fetches(conn, symbol: str, sessions: list[date], bars: list[dict[str, Any]], provider: str,
                    now: datetime) -> None:
    counts = {day: 0 for day in sessions}
    for bar in bars:
        day = parse_timestamp(bar["t"]).astimezone(MARKET_TIMEZONE).date()
        counts[day] = counts.get(day, 0) + 1
    conn.executemany("INSERT OR REPLACE INTO historical_bar_fetches VALUES (?, ?, ?, ?, ?, ?)",
                     [(symbol, day.isoformat(), TIMEFRAME, provider, count, now.isoformat())
                      for day, count in counts.items()])
