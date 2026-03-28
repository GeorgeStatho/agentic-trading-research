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
    table_names = [
        "sectors",
        "industries",
        "companies",
        "macro_events",
        "news_articles",
        "industry_news_articles",
        "company_news_articles",
        "failed_urls",
    ]
    counts: dict[str, int] = {}

    with get_connection() as conn:
        for table_name in table_names:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
            counts[table_name] = row["count"]
    return counts


def fetch_article_counts() -> dict[str, int]:
    with get_connection() as conn:
        total_articles = conn.execute(
            "SELECT COUNT(*) AS count FROM news_articles"
        ).fetchone()["count"]
        linked_articles = conn.execute(
            """
            SELECT COUNT(DISTINCT article_id) AS count
            FROM industry_news_articles
            """
        ).fetchone()["count"]
        company_linked_articles = conn.execute(
            """
            SELECT COUNT(DISTINCT article_id) AS count
            FROM company_news_articles
            """
        ).fetchone()["count"]
        industry_article_links = conn.execute(
            "SELECT COUNT(*) AS count FROM industry_news_articles"
        ).fetchone()["count"]
        company_article_links = conn.execute(
            "SELECT COUNT(*) AS count FROM company_news_articles"
        ).fetchone()["count"]

    return {
        "total_news_articles": total_articles,
        "industry_linked_articles": linked_articles,
        "industry_article_links": industry_article_links,
        "company_linked_articles": company_linked_articles,
        "company_article_links": company_article_links,
    }


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


def fetch_all_companies(limit: int | None = None) -> list[dict]:
    query = """
        SELECT
            c.id,
            c.symbol,
            c.name,
            c.rating,
            c.market_weight,
            i.industry_key,
            i.name AS industry_name,
            s.sector_key,
            s.name AS sector_name
        FROM companies AS c
        JOIN industries AS i ON i.id = c.industry_id
        JOIN sectors AS s ON s.id = i.sector_id
        ORDER BY s.name, i.name, c.name
    """

    with get_connection() as conn:
        if limit is None:
            rows = conn.execute(query).fetchall()
        else:
            rows = conn.execute(f"{query}\nLIMIT ?", (limit,)).fetchall()
    return _rows_to_dicts(rows)


def fetch_sample_articles(limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                source,
                title,
                published_at,
                source_url,
                created_at
            FROM news_articles
            ORDER BY COALESCE(published_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return _rows_to_dicts(rows)


def fetch_sample_industry_articles(limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                ina.id,
                i.industry_key,
                i.name AS industry_name,
                na.source,
                na.title,
                na.published_at,
                na.source_url,
                ina.source_page_url,
                ina.created_at
            FROM industry_news_articles AS ina
            JOIN industries AS i ON i.id = ina.industry_id
            JOIN news_articles AS na ON na.id = ina.article_id
            ORDER BY COALESCE(na.published_at, ina.created_at) DESC, ina.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return _rows_to_dicts(rows)


def fetch_sample_company_articles(limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                cna.id,
                c.symbol,
                c.name AS company_name,
                i.industry_key,
                i.name AS industry_name,
                na.source,
                na.title,
                na.published_at,
                na.source_url,
                cna.source_page_url,
                cna.created_at
            FROM company_news_articles AS cna
            JOIN companies AS c ON c.id = cna.company_id
            JOIN industries AS i ON i.id = c.industry_id
            JOIN news_articles AS na ON na.id = cna.article_id
            ORDER BY COALESCE(na.published_at, cna.created_at) DESC, cna.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return _rows_to_dicts(rows)


def fetch_sample_failed_urls(limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                url,
                normalized_url,
                stage,
                last_error,
                failure_count,
                is_permanent,
                created_at,
                updated_at
            FROM failed_urls
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return _rows_to_dicts(rows)


def run_smoke_test() -> dict:
    return {
        #"db_path": str(Path(DB_PATH).resolve()),
        #"table_counts": fetch_table_counts(),
        "article_counts": fetch_article_counts(),
        #"sample_market_data": fetch_sample_market_data(),
        #"sample_macro_events": fetch_sample_macro_events(),
        #"sample_us_macro_events": fetch_macro_events_by_country("United States"),
        "all_companies": fetch_all_companies(),
        "sample_failed_urls": fetch_sample_failed_urls(),
        #"sample_technology_companies": fetch_companies_by_industry("software-infrastructure"),
        #"sample_articles": fetch_sample_articles(),
        #"sample_industry_articles": fetch_sample_industry_articles(),
        "sample_company_articles": fetch_sample_company_articles(),
    }


if __name__ == "__main__":
    print(json.dumps(run_smoke_test(), indent=2, sort_keys=True))
