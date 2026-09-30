from __future__ import annotations

from pathlib import Path
from typing import Any

from db_common import DB_PATH, get_connection, json_text
from .bootstrap import initialize_database
import json


def record_market_regime_snapshot(snapshot: dict[str, Any], db_path: Path | str = DB_PATH) -> int:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO market_regime_runs (as_of, state, confidence, policy_version, snapshot_json)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                str(snapshot.get("as_of") or ""),
                str(snapshot.get("state") or "unknown"),
                str(snapshot.get("confidence") or "low"),
                str(snapshot.get("policy_version") or "market-regime-v1"),
                json_text(snapshot),
            ),
        )
        return int(cursor.fetchone()["id"])


def get_latest_market_regime_snapshot(db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, as_of, state, confidence, policy_version, snapshot_json, created_at FROM market_regime_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None

    record = dict(row)
    try:
        snapshot = json.loads(record.pop("snapshot_json") or "{}")
    except json.JSONDecodeError:
        snapshot = {}
    return {**record, **snapshot, "market_regime_run_id": record["id"]}


def store_regime_bars(symbol, bars, *, provider, db_path=DB_PATH):
    """Batch an entire lookback in one transaction; repeated refreshes are safe."""
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        conn.executemany("""
            INSERT INTO historical_underlying_bars
                (symbol, bar_at, timeframe, open_price, high_price, low_price, close_price, volume, provider)
            VALUES (?, ?, '1d', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, bar_at, timeframe, provider) DO UPDATE SET
                open_price=excluded.open_price, high_price=excluded.high_price,
                low_price=excluded.low_price, close_price=excluded.close_price, volume=excluded.volume
        """, [(symbol, bar["bar_at"], bar.get("open"), bar.get("high"), bar.get("low"),
               bar["close"], bar.get("volume"), provider) for bar in bars])


def load_regime_bars(symbol, *, as_of, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT bar_at, close_price, provider FROM historical_underlying_bars
            WHERE symbol = ? AND timeframe = '1d' AND close_price > 0
              AND julianday(bar_at) <= julianday(?)
            ORDER BY julianday(bar_at)
        """, (symbol, as_of.isoformat())).fetchall()
    # Keep one provider per series; mixing feeds can introduce artificial returns.
    series = {}
    for row in rows:
        series.setdefault(row["provider"], []).append(dict(row))
    if not series:
        return []
    return max(series.values(), key=lambda items: (items[-1]["bar_at"], len(items)))[-252:]
