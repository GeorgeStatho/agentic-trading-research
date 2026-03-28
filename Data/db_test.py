from __future__ import annotations

import json
from pathlib import Path

from db_common import DB_PATH, get_connection

MACRO_EVENT_OPTIONAL_COLUMNS = [
    "currency",
    "actual",
    "forecast",
    "previous",
]


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def _table_columns(table_name: str) -> set[str]:
    with get_connection() as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _macro_event_select_clause() -> str:
    columns = [
        "id",
        "source",
        "event_key",
        "event_name",
        "event_date",
        "event_time",
        "country",
        "category",
        "importance",
    ]
    available_columns = _table_columns("macro_events")
    for optional_column in MACRO_EVENT_OPTIONAL_COLUMNS:
        if optional_column in available_columns:
            columns.append(optional_column)
    return ", ".join(columns)


def fetch_table_counts() -> dict[str, int]:
    table_names = ["sectors", "industries", "companies", "macro_events", "news_articles"]
    counts: dict[str, int] = {}

    with get_connection() as conn:
        for table_name in table_names:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
            counts[table_name] = row["count"]
    return counts


def fetch_sample_market_data(limit: int = 5) -> dict[str, list[dict]]:
    with get_connection() as conn:
        sectors = conn.execute(
            """
            SELECT id, sector_key, name
            FROM sectors
            ORDER BY name
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        industries = conn.execute(
            """
            SELECT i.id, i.industry_key, i.name, i.symbol, i.market_weight, s.name AS sector_name
            FROM industries AS i
            JOIN sectors AS s ON s.id = i.sector_id
            ORDER BY i.market_weight DESC, i.name
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        companies = conn.execute(
            """
            SELECT c.id, c.symbol, c.name, c.rating, c.market_weight, i.name AS industry_name
            FROM companies AS c
            JOIN industries AS i ON i.id = c.industry_id
            ORDER BY c.market_weight DESC, c.symbol
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "sectors": _rows_to_dicts(sectors),
        "industries": _rows_to_dicts(industries),
        "companies": _rows_to_dicts(companies),
    }


def fetch_sample_macro_events(limit: int = 10) -> list[dict]:
    select_clause = _macro_event_select_clause()
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {select_clause}
            FROM macro_events
            ORDER BY event_date DESC, event_time DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return _rows_to_dicts(rows)


def fetch_macro_events_by_country(country: str, limit: int = 10) -> list[dict]:
    select_clause = _macro_event_select_clause()
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {select_clause}
            FROM macro_events
            WHERE country = ?
            ORDER BY event_date DESC, event_time DESC, id DESC
            LIMIT ?
            """,
            (country, limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def fetch_companies_by_industry(industry_key: str, limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.symbol, c.name, c.rating, c.market_weight
            FROM companies AS c
            JOIN industries AS i ON i.id = c.industry_id
            WHERE i.industry_key = ?
            ORDER BY c.market_weight DESC, c.symbol
            LIMIT ?
            """,
            (industry_key, limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def run_smoke_test() -> dict:
    return {
        "db_path": str(Path(DB_PATH).resolve()),
        "table_counts": fetch_table_counts(),
        "sample_market_data": fetch_sample_market_data(),
        "sample_macro_events": fetch_sample_macro_events(),
        "sample_us_macro_events": fetch_macro_events_by_country("United States"),
        "sample_technology_companies": fetch_companies_by_industry("software-infrastructure"),
    }


if __name__ == "__main__":
    print(json.dumps(run_smoke_test(), indent=2, sort_keys=True))
