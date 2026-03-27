from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


DATA_DIR = Path(__file__).resolve().parent
DB_PATH = DATA_DIR / "stock_experiment.db"
SCHEMA_PATH = DATA_DIR / "industries.sql"


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database(db_path: Path | str = DB_PATH, schema_path: Path | str = SCHEMA_PATH) -> None:
    schema = Path(schema_path).read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        conn.executescript(schema)


def add_industry(name: str, description: Optional[str] = None, db_path: Path | str = DB_PATH) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO industries (name, description)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET description = excluded.description
            """,
            (name, description),
        )
        return cursor.lastrowid or conn.execute(
            "SELECT id FROM industries WHERE name = ?",
            (name,),
        ).fetchone()["id"]


def add_company(
    industry_id: int,
    name: str,
    ticker: Optional[str] = None,
    headquarters: Optional[str] = None,
    market_cap: Optional[int] = None,
    db_path: Path | str = DB_PATH,
) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO companies (industry_id, name, ticker, headquarters, market_cap)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(industry_id, name) DO UPDATE SET
                ticker = excluded.ticker,
                headquarters = excluded.headquarters,
                market_cap = excluded.market_cap
            """,
            (industry_id, name, ticker, headquarters, market_cap),
        )
        return cursor.lastrowid or conn.execute(
            "SELECT id FROM companies WHERE industry_id = ? AND name = ?",
            (industry_id, name),
        ).fetchone()["id"]


def list_companies_by_industry(industry_name: str, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.ticker, c.headquarters, c.market_cap
            FROM companies AS c
            JOIN industries AS i ON i.id = c.industry_id
            WHERE i.name = ?
            ORDER BY c.name
            """,
            (industry_name,),
        ).fetchall()
    return rows


if __name__ == "__main__":
    initialize_database()
    print(f"Initialized SQLite database at {DB_PATH}")
