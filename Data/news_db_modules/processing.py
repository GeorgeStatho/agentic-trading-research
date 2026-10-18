from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from db_common import get_connection, json_text


def _mark_article_processed(
    table_name: str,
    key_columns: tuple[str, ...],
    values: tuple[object, ...],
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return _mark_article_processed(
                table_name,
                key_columns,
                values,
                conn=local_conn,
            )

    column_names = [*key_columns, "model", "raw_json"]
    columns_sql = ", ".join(column_names)
    placeholders = ", ".join("?" for _ in column_names)
    conflict_sql = ", ".join(key_columns)
    cursor = conn.execute(
        f"""
        INSERT INTO {table_name} ({columns_sql})
        VALUES ({placeholders})
        ON CONFLICT({conflict_sql}) DO UPDATE SET
            model = excluded.model,
            raw_json = excluded.raw_json,
            processed_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def mark_world_news_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    return _mark_article_processed(
        "world_news_article_processing",
        ("article_id",),
        (article_id, model, json_text(raw_json)),
        db_path=db_path,
        conn=conn,
    )


def mark_us_news_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    return _mark_article_processed(
        "us_news_article_processing",
        ("article_id",),
        (article_id, model, json_text(raw_json)),
        db_path=db_path,
        conn=conn,
    )


def mark_industry_opportunist_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    return _mark_article_processed(
        "industry_opportunist_article_processing",
        ("article_id",),
        (article_id, model, json_text(raw_json)),
        db_path=db_path,
        conn=conn,
    )


def mark_company_opportunist_article_processed(
    article_id: int,
    company_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    return _mark_article_processed(
        "company_opportunist_article_processing",
        ("article_id", "company_id"),
        (article_id, company_id, model, json_text(raw_json)),
        db_path=db_path,
        conn=conn,
    )


def mark_sector_opportunist_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    return _mark_article_processed(
        "sector_opportunist_article_processing",
        ("article_id",),
        (article_id, model, json_text(raw_json)),
        db_path=db_path,
        conn=conn,
    )
