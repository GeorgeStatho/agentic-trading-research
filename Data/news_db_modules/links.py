from __future__ import annotations

from pathlib import Path

from db_common import DB_PATH, get_connection
from .provenance import update_article_provenance


def _upsert_link(
    table_name: str,
    conflict_columns: tuple[str, ...],
    values: tuple[object, ...],
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    if conn is None:
        with get_connection(db_path) as local_conn:
            return _upsert_link(
                table_name,
                conflict_columns,
                values,
                conn=local_conn,
            )

    column_names = [*conflict_columns, "source_page_url"]
    placeholders = ", ".join("?" for _ in column_names)
    columns_sql = ", ".join(column_names)
    conflict_sql = ", ".join(conflict_columns)
    cursor = conn.execute(
        f"""
        INSERT INTO {table_name} ({columns_sql})
        VALUES ({placeholders})
        ON CONFLICT({conflict_sql}) DO UPDATE SET
            source_page_url = excluded.source_page_url
        RETURNING id
        """,
        values,
    )
    link_id = cursor.fetchone()["id"]
    if table_name == "company_news_articles":
        update_article_provenance(int(values[1]), conn=conn)
    return link_id


def link_industry_to_article(
    industry_id: int,
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_link(
        "industry_news_articles",
        ("industry_id", "article_id"),
        (industry_id, article_id, source_page_url),
        db_path=db_path,
        conn=conn,
    )


def link_company_to_article(
    company_id: int,
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_link(
        "company_news_articles",
        ("company_id", "article_id"),
        (company_id, article_id, source_page_url),
        db_path=db_path,
        conn=conn,
    )


def link_sector_to_article(
    sector_id: int,
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_link(
        "sector_news_articles",
        ("sector_id", "article_id"),
        (sector_id, article_id, source_page_url),
        db_path=db_path,
        conn=conn,
    )


def link_us_article(
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_link(
        "us_news_articles",
        ("article_id",),
        (article_id, source_page_url),
        db_path=db_path,
        conn=conn,
    )


def link_world_article(
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_link(
        "world_news_articles",
        ("article_id",),
        (article_id, source_page_url),
        db_path=db_path,
        conn=conn,
    )
