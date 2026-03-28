from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent
DB_PATH = DATA_DIR / "stock_experiment.db"


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
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
