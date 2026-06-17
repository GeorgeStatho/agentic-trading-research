from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from pathlib import Path
from typing import Any

from db_common import DATA_DIR, clean_text, coerce_float, table_columns, table_exists

SCHEMA_PATH = DATA_DIR / "market_schema.sql"
SECTOR_DEFINITIONS_PATH = DATA_DIR / "sector_etfs.json"
LEGACY_SECTOR_KEY = "unassigned"
LEGACY_SECTOR_NAME = "Unassigned"


def normalize_timestamp(value: Any) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()

    text = clean_text(value)
    if text is None:
        return datetime.now(timezone.utc).isoformat()

    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return datetime.now(timezone.utc).isoformat()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def extract_snapshot_price(snapshot: dict[str, Any]) -> float | None:
    for key in ("price", "last_price", "lastPrice", "regularMarketPrice", "ask_price", "bid_price"):
        value = coerce_float(snapshot.get(key))
        if value is not None:
            return value
    return None


def extract_snapshot_volume(snapshot: dict[str, Any]) -> float | None:
    for key in ("volume", "day_volume", "dayVolume", "regularMarketVolume"):
        value = coerce_float(snapshot.get(key))
        if value is not None:
            return value
    return None


def coerce_int(value: Any) -> int | None:
    text = clean_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def normalize_bool_to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = clean_text(value)
    if text is None:
        return 0
    return 1 if text.lower() in {"true", "yes", "1"} else 0


def find_company_row_by_symbol(
    symbol: str | None,
    *,
    conn: sqlite3.Connection,
) -> sqlite3.Row | None:
    normalized_symbol = clean_text(symbol)
    if normalized_symbol is None:
        return None
    return conn.execute(
        """
        SELECT id, symbol, name
        FROM companies
        WHERE symbol = ?
        LIMIT 1
        """,
        (normalized_symbol,),
    ).fetchone()


def is_legacy_schema(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, "industries"):
        return False
    return "sector_id" not in table_columns(conn, "industries")
