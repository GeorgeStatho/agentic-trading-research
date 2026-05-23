import json
from pathlib import Path

from db_common import DATA_DIR, get_connection, table_exists
from market_db import (
    initialize_database as initialize_market_database,
    load_sector_definitions_from_json,
)


SECTOR_DEFINITIONS_JSON_PATH = DATA_DIR / "sector_etfs.json"


def _ensure_market_reference_data() -> None:
    initialize_market_database()
    if not SECTOR_DEFINITIONS_JSON_PATH.exists():
        return
    if not Path(SECTOR_DEFINITIONS_JSON_PATH).read_text(encoding="utf-8").strip():
        return

    with get_connection() as conn:
        if not table_exists(conn, "sectors"):
            initialize_market_database()
            return

        existing_sector_keys = {
            str(row["sector_key"]).strip().lower()
            for row in conn.execute("SELECT sector_key FROM sectors").fetchall()
        }

    sector_definition_keys = {
        str(key).strip().lower()
        for key in load_json_keys(SECTOR_DEFINITIONS_JSON_PATH)
    }

    if sector_definition_keys.difference(existing_sector_keys):
        load_sector_definitions_from_json(SECTOR_DEFINITIONS_JSON_PATH)


def load_json_keys(json_path: Path) -> list[str]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    return [str(key) for key in payload.keys()]


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
