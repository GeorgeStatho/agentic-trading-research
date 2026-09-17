from __future__ import annotations

import sqlite3
from pathlib import Path

from db_common import DB_PATH, coerce_float, get_connection
from .entities import add_company, add_industry, add_sector
from .shared import LEGACY_SECTOR_KEY, LEGACY_SECTOR_NAME, SCHEMA_PATH, is_legacy_schema


_MANAGER_DECISION_OUTCOME_COLUMNS = {
    "exit_reason": "TEXT",
    "closing_order_ids_json": "TEXT",
    "outcome_status": "TEXT",
    "realized_pnl_pct": "REAL",
    "closed_at": "TEXT",
}


def _ensure_manager_decision_outcome_columns(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'manager_decision_history'"
    ).fetchone()
    if table_exists is None:
        return
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(manager_decision_history)").fetchall()
    }
    for column_name, column_type in _MANAGER_DECISION_OUTCOME_COLUMNS.items():
        if column_name not in columns:
            conn.execute(f"ALTER TABLE manager_decision_history ADD COLUMN {column_name} {column_type}")


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    legacy_industries = []
    legacy_companies = []

    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'industries'"
    ).fetchone():
        legacy_industries = [dict(row) for row in conn.execute("SELECT * FROM industries").fetchall()]
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'companies'"
    ).fetchone():
        legacy_companies = [dict(row) for row in conn.execute("SELECT * FROM companies").fetchall()]

    conn.execute("DROP TABLE IF EXISTS companies")
    conn.execute("DROP TABLE IF EXISTS industries")
    conn.execute("DROP TABLE IF EXISTS sectors")
    conn.executescript(Path(SCHEMA_PATH).read_text(encoding="utf-8"))

    if not legacy_industries:
        return

    sector_id = add_sector(LEGACY_SECTOR_KEY, LEGACY_SECTOR_NAME, conn=conn)
    industry_id_map: dict[int, int] = {}

    for industry in legacy_industries:
        new_id = add_industry(
            sector_id,
            industry_key=str(industry["name"]).strip().lower().replace(" ", "-"),
            name=industry["name"],
            raw_json=industry,
            conn=conn,
        )
        industry_id_map[industry["id"]] = new_id

    for company in legacy_companies:
        legacy_industry_id = company.get("industry_id")
        if legacy_industry_id not in industry_id_map:
            continue

        raw_json = {
            "headquarters": company.get("headquarters"),
            "market_cap": company.get("market_cap"),
        }
        add_company(
            industry_id_map[legacy_industry_id],
            symbol=company.get("ticker") or company["name"],
            name=company["name"],
            market_weight=coerce_float(company.get("market_cap")),
            raw_json=raw_json,
            conn=conn,
        )


def initialize_database(
    db_path: Path | str = DB_PATH,
    schema_path: Path | str = SCHEMA_PATH,
) -> None:
    with get_connection(db_path) as conn:
        if is_legacy_schema(conn):
            _migrate_legacy_schema(conn)
        else:
            conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
        _ensure_manager_decision_outcome_columns(conn)
        from .telemetry_schema import migrate_telemetry_columns
        migrate_telemetry_columns(conn)
