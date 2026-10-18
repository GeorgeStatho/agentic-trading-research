from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Any, Optional

from db_common import get_connection, json_text


def add_sector(
    sector_key: str,
    name: str,
    raw_json: Any | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (sector_key, name, json_text(raw_json))
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
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
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (sector_id, industry_key, name, symbol, market_weight, json_text(raw_json))
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
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
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (industry_id, symbol, name, rating, market_weight, json_text(raw_json))
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
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


def replace_industry_company_rankings(
    industry_id: int,
    ranking_type: str,
    ranked_companies: list[tuple[int, int, Any | None]],
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            replace_industry_company_rankings(
                industry_id,
                ranking_type,
                ranked_companies,
                conn=local_conn,
            )
            return

    conn.execute(
        """
        DELETE FROM industry_company_rankings
        WHERE industry_id = ? AND ranking_type = ?
        """,
        (industry_id, ranking_type),
    )

    for company_id, rank, raw_json in ranked_companies:
        conn.execute(
            """
            INSERT INTO industry_company_rankings (
                industry_id,
                company_id,
                ranking_type,
                rank,
                raw_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (industry_id, company_id, ranking_type, rank, json_text(raw_json)),
        )
