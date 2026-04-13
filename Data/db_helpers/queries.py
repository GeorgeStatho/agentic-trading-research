from pathlib import Path

from db_common import DATA_DIR, get_connection, table_exists
from market_db import (
    initialize_database as initialize_market_database,
    load_sector_tree_from_json,
)


SECTOR_TREE_JSON_PATH = DATA_DIR / "sectors_companies.json"


def _ensure_market_reference_data() -> None:
    initialize_market_database()

    with get_connection() as conn:
        if not table_exists(conn, "sectors"):
            initialize_market_database()
            return

        has_sector_rows = conn.execute("SELECT 1 FROM sectors LIMIT 1").fetchone() is not None

    if has_sector_rows:
        return

    if not SECTOR_TREE_JSON_PATH.exists():
        return

    if not Path(SECTOR_TREE_JSON_PATH).read_text(encoding="utf-8").strip():
        return

    load_sector_tree_from_json(SECTOR_TREE_JSON_PATH)


def get_all_sectors() -> list[dict]:
    _ensure_market_reference_data()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                sector_key,
                name
            FROM sectors
            ORDER BY name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_industries() -> list[dict]:
    _ensure_market_reference_data()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                i.id,
                i.industry_key,
                i.name,
                i.symbol,
                i.market_weight,
                s.sector_key,
                s.name AS sector_name
            FROM industries AS i
            JOIN sectors AS s ON s.id = i.sector_id
            ORDER BY s.name, i.name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_companies() -> list[dict]:
    _ensure_market_reference_data()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.symbol,
                c.name,
                c.rating,
                c.market_weight,
                c.raw_json,
                i.id AS industry_id,
                i.industry_key,
                i.name AS industry_name,
                s.id AS sector_id,
                s.sector_key,
                s.name AS sector_name
            FROM companies AS c
            JOIN industries AS i ON i.id = c.industry_id
            JOIN sectors AS s ON s.id = i.sector_id
            ORDER BY s.name, i.name, c.name
            """
        ).fetchall()
    return [dict(row) for row in rows]
