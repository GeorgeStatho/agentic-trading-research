from __future__ import annotations

from contextlib import closing
from pathlib import Path

from db_common import get_connection
from .bootstrap import initialize_database
from .shared import normalize_url


def list_industry_news_articles(industry_id: int, db_path: Path | str | None = None) -> list[dict]:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        rows = conn.execute(
            """
            SELECT
                na.id,
                na.source,
                na.article_key,
                na.title,
                na.summary,
                content.body,
                na.published_at,
                na.section,
                na.source_url,
                ina.source_page_url
            FROM industry_news_articles AS ina
            JOIN news_articles AS na ON na.id = ina.article_id
            LEFT JOIN news_article_content AS content ON content.article_id = na.id
            WHERE ina.industry_id = ?
            ORDER BY na.created_at DESC, na.id DESC
            """,
            (industry_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_company_news_articles(company_id: int, db_path: Path | str | None = None) -> list[dict]:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        rows = conn.execute(
            """
            SELECT
                na.id,
                na.source,
                na.article_key,
                na.title,
                na.summary,
                content.body,
                na.published_at,
                na.section,
                na.source_url,
                cna.source_page_url
            FROM company_news_articles AS cna
            JOIN news_articles AS na ON na.id = cna.article_id
            LEFT JOIN news_article_content AS content ON content.article_id = na.id
            WHERE cna.company_id = ?
            ORDER BY na.created_at DESC, na.id DESC
            """,
            (company_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_saved_and_failed_urls(
    normalized_urls: list[str], db_path: Path | str | None = None
) -> tuple[dict[str, dict], dict[str, dict]]:
    # Look up many normalized urls at once: saved articles (lowest id wins) and failed_urls rows.
    if not normalized_urls:
        return {}, {}

    initialize_database(db_path=db_path)
    saved: dict[str, dict] = {}
    failed: dict[str, dict] = {}
    with closing(get_connection(db_path)) as conn:
        for start in range(0, len(normalized_urls), 500):
            chunk = normalized_urls[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT id, normalized_url, published_at FROM news_articles "
                f"WHERE normalized_url IN ({placeholders}) ORDER BY id",
                chunk,
            ):
                saved.setdefault(row["normalized_url"], {"id": row["id"], "published_at": row["published_at"]})
            for row in conn.execute(
                f"SELECT normalized_url, failure_count, is_permanent FROM failed_urls "
                f"WHERE normalized_url IN ({placeholders})",
                chunk,
            ):
                failed[row["normalized_url"]] = {
                    "failure_count": row["failure_count"],
                    "is_permanent": row["is_permanent"],
                }
    return saved, failed


def add_failed_url(
    url: str,
    stage: str | None = None,
    error: str | None = None,
    is_permanent: bool = False,
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    normalized = normalize_url(url)
    if not normalized:
        return 0

    values = (
        url,
        normalized,
        stage,
        error,
        1 if is_permanent else 0,
    )
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return add_failed_url(
                url,
                stage=stage,
                error=error,
                is_permanent=is_permanent,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO failed_urls (
            url, normalized_url, stage, last_error, failure_count, is_permanent, updated_at
        )
        VALUES (?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(normalized_url) DO UPDATE SET
            url = excluded.url,
            stage = excluded.stage,
            last_error = excluded.last_error,
            failure_count = failed_urls.failure_count + 1,
            is_permanent = CASE
                WHEN excluded.is_permanent = 1 OR failed_urls.is_permanent = 1 THEN 1
                ELSE 0
            END,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def remove_failed_url(url: str, db_path: Path | str | None = None, conn=None) -> None:
    normalized = normalize_url(url)
    if not normalized:
        return

    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            remove_failed_url(url, conn=local_conn)
            return

    conn.execute(
        """
        DELETE FROM failed_urls
        WHERE normalized_url = ?
        """,
        (normalized,),
    )
