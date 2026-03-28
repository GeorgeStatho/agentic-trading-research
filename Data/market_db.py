from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from db_common import DB_PATH, DATA_DIR, clean_text, coerce_float, get_connection, initialize_schema, json_text, table_columns, table_exists


SCHEMA_PATH = DATA_DIR / "market_schema.sql"
LEGACY_SECTOR_KEY = "unassigned"
LEGACY_SECTOR_NAME = "Unassigned"


def _is_legacy_schema(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, "industries"):
        return False
    return "sector_id" not in table_columns(conn, "industries")


def add_sector(
    sector_key: str,
    name: str,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (sector_key, name, json_text(raw_json))
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_sector(sector_key, name, raw_json=raw_json, conn=local_conn)

    cursor = conn.execute(
        """
        INSERT INTO sectors (sector_key, name, raw_json)
        VALUES (?, ?, ?)
        ON CONFLICT(sector_key) DO UPDATE SET
            name = excluded.name,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_industry(
    sector_id: int,
    industry_key: str,
    name: str,
    symbol: Optional[str] = None,
    market_weight: Optional[float] = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (sector_id, industry_key, name, symbol, market_weight, json_text(raw_json))
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_industry(
                sector_id,
                industry_key,
                name,
                symbol=symbol,
                market_weight=market_weight,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO industries (sector_id, industry_key, name, symbol, market_weight, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(industry_key) DO UPDATE SET
            sector_id = excluded.sector_id,
            name = excluded.name,
            symbol = excluded.symbol,
            market_weight = excluded.market_weight,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_company(
    industry_id: int,
    symbol: str,
    name: str,
    rating: Optional[str] = None,
    market_weight: Optional[float] = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (industry_id, symbol, name, rating, market_weight, json_text(raw_json))
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_company(
                industry_id,
                symbol,
                name,
                rating=rating,
                market_weight=market_weight,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO companies (industry_id, symbol, name, rating, market_weight, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            industry_id = excluded.industry_id,
            name = excluded.name,
            rating = excluded.rating,
            market_weight = excluded.market_weight,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    legacy_industries = []
    legacy_companies = []

    if table_exists(conn, "industries"):
        legacy_industries = [dict(row) for row in conn.execute("SELECT * FROM industries").fetchall()]
    if table_exists(conn, "companies"):
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


def initialize_database(db_path: Path | str = DB_PATH, schema_path: Path | str = SCHEMA_PATH) -> None:
    with get_connection(db_path) as conn:
        if _is_legacy_schema(conn):
            _migrate_legacy_schema(conn)
        else:
            conn.executescript(Path(schema_path).read_text(encoding="utf-8"))


def load_sector_tree(
    sector_tree: dict[str, dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)

    with get_connection(db_path) as conn:
        for sector_key, sector_payload in sector_tree.items():
            industries_payload = sector_payload
            sector_name = sector_key.replace("-", " ").title()
            sector_raw = None

            if isinstance(sector_payload, dict) and "industries" in sector_payload:
                industries_payload = sector_payload.get("industries", {})
                sector_name = sector_payload.get("name", sector_name)
                sector_raw = sector_payload

            sector_id = add_sector(sector_key, sector_name, raw_json=sector_raw, conn=conn)

            if not isinstance(industries_payload, dict):
                continue

            for industry_key, industry_payload in industries_payload.items():
                if not isinstance(industry_payload, dict):
                    continue

                industry_id = add_industry(
                    sector_id,
                    industry_key=industry_key,
                    name=industry_payload.get("name", industry_key.replace("-", " ").title()),
                    symbol=industry_payload.get("symbol"),
                    market_weight=coerce_float(industry_payload.get("market weight") or industry_payload.get("market_weight")),
                    raw_json=industry_payload,
                    conn=conn,
                )

                companies_payload = industry_payload.get("companies", {})
                if not isinstance(companies_payload, dict):
                    continue

                for company_symbol, company_payload in companies_payload.items():
                    if not isinstance(company_payload, dict):
                        continue

                    normalized_symbol = clean_text(company_payload.get("symbol")) or clean_text(company_symbol)
                    normalized_name = clean_text(company_payload.get("name")) or normalized_symbol
                    if normalized_symbol is None or normalized_name is None:
                        continue

                    add_company(
                        industry_id,
                        symbol=normalized_symbol,
                        name=normalized_name,
                        rating=clean_text(company_payload.get("rating")),
                        market_weight=coerce_float(company_payload.get("market weight") or company_payload.get("market_weight")),
                        raw_json=company_payload,
                        conn=conn,
                    )


def load_sector_tree_from_json(
    json_path: Path | str,
    db_path: Path | str = DB_PATH,
) -> None:
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Top-level JSON payload must be a dictionary.")
    load_sector_tree(payload, db_path=db_path)


def list_companies_by_industry(industry_key: str, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.symbol, c.name, c.rating, c.market_weight
            FROM companies AS c
            JOIN industries AS i ON i.id = c.industry_id
            WHERE i.industry_key = ?
            ORDER BY c.market_weight DESC, c.symbol
            """,
            (industry_key,),
        ).fetchall()
    return rows


if __name__ == "__main__":
    initialize_database()
    json_path = DATA_DIR / "sectors_companies.json"
    if json_path.exists() and json_path.read_text(encoding="utf-8").strip():
        load_sector_tree_from_json(json_path)
        print(f"Loaded sector tree from {json_path} into {DB_PATH}")
    else:
        print(f"Initialized market database at {DB_PATH}")
