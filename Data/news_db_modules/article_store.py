from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from db_common import get_connection, json_text
from .provenance import update_article_provenance
from .shared import (
    build_content_hash,
    coerce_float,
    coerce_int,
    normalize_title,
    normalize_url,
    score_from_raw_json,
)


def add_news_article(
    source: str,
    article_key: str,
    title: str,
    source_url: str,
    summary: str | None = None,
    body: str | None = None,
    published_at: str | None = None,
    section: str | None = None,
    age_days: float | None = None,
    recency_score: float | None = None,
    source_reputation_score: float | None = None,
    directness_score: float | None = None,
    confirmation_score: float | None = None,
    independent_source_count: int | None = None,
    factuality_score: float | None = None,
    evidence_score: float | None = None,
    raw_json: Any | None = None,
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    normalized_url = normalize_url(source_url)
    normalized_title = normalize_title(title)
    content_hash = build_content_hash(body or "")
    age_days = coerce_float(
        age_days if age_days is not None else score_from_raw_json(raw_json, "age_days")
    )
    recency_score = coerce_float(
        recency_score if recency_score is not None else score_from_raw_json(raw_json, "recency_score")
    )
    source_reputation_score = coerce_float(
        source_reputation_score
        if source_reputation_score is not None
        else score_from_raw_json(raw_json, "source_reputation_score")
    )
    directness_score = coerce_float(
        directness_score
        if directness_score is not None
        else score_from_raw_json(raw_json, "directness_score")
    )
    confirmation_score = coerce_float(
        confirmation_score
        if confirmation_score is not None
        else score_from_raw_json(raw_json, "confirmation_score")
    )
    independent_source_count = coerce_int(
        independent_source_count
        if independent_source_count is not None
        else score_from_raw_json(raw_json, "independent_source_count")
    )
    factuality_score = coerce_float(
        factuality_score
        if factuality_score is not None
        else score_from_raw_json(raw_json, "factuality_score")
    )
    evidence_score = coerce_float(
        evidence_score
        if evidence_score is not None
        else score_from_raw_json(raw_json, "evidence_score")
    )

    values = (
        source,
        article_key,
        title,
        normalized_title,
        summary,
        normalized_url,
        content_hash,
        published_at,
        section,
        age_days,
        recency_score,
        source_reputation_score,
        directness_score,
        confirmation_score,
        independent_source_count,
        factuality_score,
        evidence_score,
        source_url,
    )
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return add_news_article(
                source,
                article_key,
                title,
                source_url,
                summary=summary,
                body=body,
                published_at=published_at,
                section=section,
                age_days=age_days,
                recency_score=recency_score,
                source_reputation_score=source_reputation_score,
                directness_score=directness_score,
                confirmation_score=confirmation_score,
                independent_source_count=independent_source_count,
                factuality_score=factuality_score,
                evidence_score=evidence_score,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        SELECT id
        FROM news_articles
        WHERE normalized_url = ?
           OR article_key = ?
        ORDER BY id
        LIMIT 1
        """,
        (normalized_url, article_key),
    )
    existing_row = cursor.fetchone()
    if existing_row is not None:
        article_id = existing_row["id"]
        conn.execute(
            """
            UPDATE news_articles
            SET source = ?,
                article_key = ?,
                title = ?,
                normalized_title = ?,
                summary = ?,
                normalized_url = ?,
                content_hash = ?,
                published_at = ?,
                section = ?,
                age_days = ?,
                recency_score = ?,
                source_reputation_score = ?,
                directness_score = ?,
                confirmation_score = ?,
                independent_source_count = ?,
                factuality_score = ?,
                evidence_score = ?,
                source_url = ?
            WHERE id = ?
            """,
            values + (article_id,),
        )
        _store_article_content(conn, article_id, body, raw_json)
        update_article_provenance(article_id, conn=conn)
        return article_id

    cursor = conn.execute(
        """
        INSERT INTO news_articles (
            source, article_key, title, normalized_title, summary,
            normalized_url, content_hash, published_at, section,
            age_days, recency_score, source_reputation_score, directness_score,
            confirmation_score, independent_source_count, factuality_score,
            evidence_score, source_url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_key) DO UPDATE SET
            source = excluded.source,
            title = excluded.title,
            normalized_title = excluded.normalized_title,
            summary = excluded.summary,
            normalized_url = excluded.normalized_url,
            content_hash = excluded.content_hash,
            published_at = excluded.published_at,
            section = excluded.section,
            age_days = excluded.age_days,
            recency_score = excluded.recency_score,
            source_reputation_score = excluded.source_reputation_score,
            directness_score = excluded.directness_score,
            confirmation_score = excluded.confirmation_score,
            independent_source_count = excluded.independent_source_count,
            factuality_score = excluded.factuality_score,
            evidence_score = excluded.evidence_score,
            source_url = excluded.source_url
        RETURNING id
        """,
        values,
    )
    article_id = cursor.fetchone()["id"]
    _store_article_content(conn, article_id, body, raw_json)
    update_article_provenance(article_id, conn=conn)
    return article_id


def _store_article_content(conn, article_id: int, body: str | None, raw_json: Any | None) -> None:
    conn.execute(
        """
        INSERT INTO news_article_content (article_id, body, raw_json)
        VALUES (?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            body = excluded.body,
            raw_json = excluded.raw_json
        """,
        (article_id, body, json_text(raw_json)),
    )
