from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Any

from db_common import clean_text, coerce_float, get_connection, json_text
from .shared import extract_snapshot_price, extract_snapshot_volume, normalize_timestamp


def add_company_price_snapshot(
    symbol: str,
    snapshot: dict[str, Any],
    *,
    source: str = "yfinance_stream",
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    normalized_symbol = clean_text(symbol)
    if normalized_symbol is None:
        return None

    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return add_company_price_snapshot(
                normalized_symbol,
                snapshot,
                source=source,
                conn=local_conn,
            )

    company_row = conn.execute(
        """
        SELECT id
        FROM companies
        WHERE symbol = ?
        """,
        (normalized_symbol,),
    ).fetchone()
    if company_row is None:
        return None

    company_id = int(company_row["id"])
    captured_at = normalize_timestamp(
        snapshot.get("captured_at")
        or snapshot.get("timestamp")
        or snapshot.get("time")
        or snapshot.get("market_time")
    )
    price = extract_snapshot_price(snapshot)
    volume = extract_snapshot_volume(snapshot)

    previous_row = conn.execute(
        """
        SELECT price
        FROM company_price_snapshots
        WHERE company_id = ?
        ORDER BY captured_at DESC, id DESC
        LIMIT 1
        """,
        (company_id,),
    ).fetchone()
    previous_price = coerce_float(previous_row["price"]) if previous_row is not None else None

    price_change = None
    price_change_pct = None
    if price is not None and previous_price is not None:
        price_change = price - previous_price
        if previous_price != 0:
            price_change_pct = price_change / previous_price

    cursor = conn.execute(
        """
        INSERT INTO company_price_snapshots (
            company_id,
            symbol,
            captured_at,
            price,
            previous_price,
            price_change,
            price_change_pct,
            volume,
            source,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            company_id,
            normalized_symbol,
            captured_at,
            price,
            previous_price,
            price_change,
            price_change_pct,
            volume,
            source,
            json_text(snapshot),
        ),
    )
    return int(cursor.fetchone()["id"])
