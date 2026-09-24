from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = DATA_DIR / "stock_experiment.db"
DB_PATH = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Several containers (worker, news_collector, option_manager, api) plus the scrape
# subprocesses all write to this one database file, so a connection has to be willing
# to wait its turn instead of failing straight away with "database is locked".
DB_TIMEOUT_SECONDS = float(os.getenv("DB_TIMEOUT_SECONDS", "30"))
_WAL_ENABLED_PATHS: set[str] = set()


def _enable_wal(conn: sqlite3.Connection, db_key: str) -> None:
    # WAL is stored in the database header, so this only has to win once per file.
    if db_key in _WAL_ENABLED_PATHS:
        return
    try:
        row = conn.execute("PRAGMA journal_mode = WAL").fetchone()
    except sqlite3.OperationalError:
        return
    if row is not None and str(row[0]).lower() == "wal":
        _WAL_ENABLED_PATHS.add(db_key)


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    # Writes here always read before they insert, so a deferred transaction would have to
    # upgrade its lock mid-flight and can lose that race outright rather than waiting out
    # the busy timeout. IMMEDIATE takes the write lock up front. Python only emits BEGIN
    # ahead of DML, so read-only callers still never take a write lock.
    conn.isolation_level = "IMMEDIATE"
    _enable_wal(conn, str(db_path))
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_schema(db_path: Path | str, schema_path: Path | str) -> None:
    schema = Path(schema_path).read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        conn.executescript(schema)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def validate_sql_identifier(value: str) -> str:
    identifier = str(value or "").strip()
    if not SQL_IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return identifier


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    safe_table_name = validate_sql_identifier(table_name)
    rows = conn.execute(f"PRAGMA table_info({safe_table_name})").fetchall()
    return {row["name"] for row in rows}


def json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None
