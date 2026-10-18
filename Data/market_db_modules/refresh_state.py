from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Any

from db_common import clean_text, get_connection, json_text
from .shared import normalize_timestamp


def get_market_data_refresh_state(
    resource_type: str,
    resource_key: str,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    normalized_type = clean_text(resource_type)
    normalized_key = clean_text(resource_key)
    if normalized_type is None or normalized_key is None:
        return None

    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return get_market_data_refresh_state(
                normalized_type,
                normalized_key,
                conn=local_conn,
            )

    return conn.execute(
        """
        SELECT resource_type, resource_key, last_hydrated_at, raw_json, created_at
        FROM market_data_refresh_state
        WHERE resource_type = ? AND resource_key = ?
        LIMIT 1
        """,
        (normalized_type, normalized_key),
    ).fetchone()


def record_market_data_refresh_state(
    resource_type: str,
    resource_key: str,
    *,
    last_hydrated_at: Any | None = None,
    raw_json: Any | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    normalized_type = clean_text(resource_type)
    normalized_key = clean_text(resource_key)
    if normalized_type is None or normalized_key is None:
        return

    values = (
        normalized_type,
        normalized_key,
        normalize_timestamp(last_hydrated_at),
        json_text(raw_json),
    )

    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            record_market_data_refresh_state(
                normalized_type,
                normalized_key,
                last_hydrated_at=last_hydrated_at,
                raw_json=raw_json,
                conn=local_conn,
            )
            return

    conn.execute(
        """
        INSERT INTO market_data_refresh_state (resource_type, resource_key, last_hydrated_at, raw_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(resource_type, resource_key) DO UPDATE SET
            last_hydrated_at = excluded.last_hydrated_at,
            raw_json = excluded.raw_json
        """,
        values,
    )
