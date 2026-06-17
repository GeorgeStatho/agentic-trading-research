from __future__ import annotations

from pathlib import Path
from typing import Any

from db_common import DB_PATH, get_connection, json_text


def _upsert_related_record(
    table_name: str,
    key_columns: tuple[str, ...],
    payload_columns: tuple[str, ...],
    values: tuple[object, ...],
    db_path: Path | str = DB_PATH,
    extra_update_sql: str | None = None,
    conn=None,
) -> int:
    if conn is None:
        with get_connection(db_path) as local_conn:
            return _upsert_related_record(
                table_name,
                key_columns,
                payload_columns,
                values,
                extra_update_sql=extra_update_sql,
                conn=local_conn,
            )

    all_columns = [*key_columns, *payload_columns]
    columns_sql = ", ".join(all_columns)
    placeholders = ", ".join("?" for _ in all_columns)
    conflict_sql = ", ".join(key_columns)
    updates = [f"{column} = excluded.{column}" for column in payload_columns]
    if extra_update_sql:
        updates.append(extra_update_sql)
    updates_sql = ", ".join(updates)
    cursor = conn.execute(
        f"""
        INSERT INTO {table_name} ({columns_sql})
        VALUES ({placeholders})
        ON CONFLICT({conflict_sql}) DO UPDATE SET
            {updates_sql}
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_world_news_sector_impact(
    article_id: int,
    sector_id: int,
    confidence: str | None = None,
    reason: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_related_record(
        "world_news_sector_impacts",
        ("article_id", "sector_id"),
        ("confidence", "reason", "raw_json"),
        (article_id, sector_id, confidence, reason, json_text(raw_json)),
        db_path=db_path,
        conn=conn,
    )


def add_us_news_sector_impact(
    article_id: int,
    sector_id: int,
    confidence: str | None = None,
    reason: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_related_record(
        "us_news_sector_impacts",
        ("article_id", "sector_id"),
        ("confidence", "reason", "raw_json"),
        (article_id, sector_id, confidence, reason, json_text(raw_json)),
        db_path=db_path,
        conn=conn,
    )


def add_industry_opportunist_impact(
    article_id: int,
    industry_id: int,
    confidence: str | None = None,
    impact_direction: str | None = None,
    impact_magnitude: str | None = None,
    reason: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_related_record(
        "industry_opportunist_impacts",
        ("article_id", "industry_id"),
        ("confidence", "impact_direction", "impact_magnitude", "reason", "raw_json"),
        (
            article_id,
            industry_id,
            confidence,
            impact_direction,
            impact_magnitude,
            reason,
            json_text(raw_json),
        ),
        db_path=db_path,
        conn=conn,
    )


def add_company_opportunist_impact(
    article_id: int,
    company_id: int,
    confidence: str | None = None,
    impact_direction: str | None = None,
    impact_magnitude: str | None = None,
    reason: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_related_record(
        "company_opportunist_impacts",
        ("article_id", "company_id"),
        ("confidence", "impact_direction", "impact_magnitude", "reason", "raw_json"),
        (
            article_id,
            company_id,
            confidence,
            impact_direction,
            impact_magnitude,
            reason,
            json_text(raw_json),
        ),
        db_path=db_path,
        conn=conn,
    )


def add_sector_opportunist_impact(
    article_id: int,
    sector_id: int,
    confidence: str | None = None,
    impact_direction: str | None = None,
    impact_magnitude: str | None = None,
    reason: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_related_record(
        "sector_opportunist_impacts",
        ("article_id", "sector_id"),
        ("confidence", "impact_direction", "impact_magnitude", "reason", "raw_json"),
        (
            article_id,
            sector_id,
            confidence,
            impact_direction,
            impact_magnitude,
            reason,
            json_text(raw_json),
        ),
        db_path=db_path,
        conn=conn,
    )


def add_strategist_company_summary(
    company_id: int,
    decision: str | None = None,
    confidence: str | None = None,
    summary: str | None = None,
    thesis: Any | None = None,
    risks: Any | None = None,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    return _upsert_related_record(
        "strategist_company_summaries",
        ("company_id",),
        ("decision", "confidence", "summary", "thesis", "risks", "model", "raw_json"),
        (
            company_id,
            decision,
            confidence,
            summary,
            json_text(thesis),
            json_text(risks),
            model,
            json_text(raw_json),
        ),
        db_path=db_path,
        extra_update_sql="updated_at = CURRENT_TIMESTAMP",
        conn=conn,
    )
