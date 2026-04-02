from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

from db_common import DB_PATH, DATA_DIR, clean_text, get_connection, initialize_schema, json_text, table_columns, table_exists

WEBSCRAPING_DIR = DATA_DIR.parent / "Python Scripts" / "Webscraping"
if str(WEBSCRAPING_DIR) not in sys.path:
    sys.path.append(str(WEBSCRAPING_DIR))

from news_normalization import build_content_hash, normalize_title, normalize_url

SCHEMA_PATH = DATA_DIR / "news_schema.sql"

MACRO_EVENT_COLUMN_TYPES = {
    "currency": "TEXT",
    "actual": "TEXT",
    "forecast": "TEXT",
    "previous": "TEXT",
}
NEWS_ARTICLE_COLUMN_TYPES = {
    "normalized_title": "TEXT",
    "normalized_url": "TEXT",
    "content_hash": "TEXT",
    "age_days": "REAL",
    "recency_score": "REAL",
    "source_reputation_score": "REAL",
    "directness_score": "REAL",
    "confirmation_score": "REAL",
    "independent_source_count": "INTEGER",
    "factuality_score": "REAL",
    "evidence_score": "REAL",
}


def _score_from_raw_json(raw_json: Any | None, key: str) -> Any | None:
    if not isinstance(raw_json, dict):
        return None
    scores = raw_json.get("scores")
    if not isinstance(scores, dict):
        return None
    return scores.get(key)


def _coerce_float(value: Any | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def initialize_database(db_path: Path | str = DB_PATH, schema_path: Path | str = SCHEMA_PATH) -> None:
    with get_connection(db_path) as conn:
        if table_exists(conn, "macro_events"):
            existing_columns = table_columns(conn, "macro_events")
            for column_name, column_type in MACRO_EVENT_COLUMN_TYPES.items():
                if column_name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE macro_events ADD COLUMN {column_name} {column_type}"
                    )
        if table_exists(conn, "news_articles"):
            existing_columns = table_columns(conn, "news_articles")
            for column_name, column_type in NEWS_ARTICLE_COLUMN_TYPES.items():
                if column_name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE news_articles ADD COLUMN {column_name} {column_type}"
                    )

    initialize_schema(db_path, schema_path)

    with get_connection(db_path) as conn:
        if table_exists(conn, "macro_events"):
            existing_columns = table_columns(conn, "macro_events")
            for column_name, column_type in MACRO_EVENT_COLUMN_TYPES.items():
                if column_name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE macro_events ADD COLUMN {column_name} {column_type}"
                    )
        if table_exists(conn, "news_articles"):
            existing_columns = table_columns(conn, "news_articles")
            for column_name, column_type in NEWS_ARTICLE_COLUMN_TYPES.items():
                if column_name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE news_articles ADD COLUMN {column_name} {column_type}"
                    )


def add_macro_event(
    source: str,
    event_key: str,
    event_name: str,
    event_date: str | None = None,
    event_time: str | None = None,
    country: str | None = None,
    currency: str | None = None,
    category: str | None = None,
    importance: str | None = None,
    actual: str | None = None,
    forecast: str | None = None,
    previous: str | None = None,
    source_url: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        source,
        event_key,
        event_name,
        event_date,
        event_time,
        country,
        currency,
        category,
        importance,
        actual,
        forecast,
        previous,
        source_url,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_macro_event(
                source,
                event_key,
                event_name,
                event_date=event_date,
                event_time=event_time,
                country=country,
                currency=currency,
                category=category,
                importance=importance,
                actual=actual,
                forecast=forecast,
                previous=previous,
                source_url=source_url,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO macro_events (
            source, event_key, event_name, event_date, event_time,
            country, currency, category, importance, actual, forecast, previous, source_url, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_key) DO UPDATE SET
            source = excluded.source,
            event_name = excluded.event_name,
            event_date = excluded.event_date,
            event_time = excluded.event_time,
            country = excluded.country,
            currency = excluded.currency,
            category = excluded.category,
            importance = excluded.importance,
            actual = excluded.actual,
            forecast = excluded.forecast,
            previous = excluded.previous,
            source_url = excluded.source_url,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


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
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    normalized_url = normalize_url(source_url)
    normalized_title = normalize_title(title)
    content_hash = build_content_hash(body or "")
    age_days = _coerce_float(age_days if age_days is not None else _score_from_raw_json(raw_json, "age_days"))
    recency_score = _coerce_float(recency_score if recency_score is not None else _score_from_raw_json(raw_json, "recency_score"))
    source_reputation_score = _coerce_float(
        source_reputation_score if source_reputation_score is not None else _score_from_raw_json(raw_json, "source_reputation_score")
    )
    directness_score = _coerce_float(
        directness_score if directness_score is not None else _score_from_raw_json(raw_json, "directness_score")
    )
    confirmation_score = _coerce_float(
        confirmation_score if confirmation_score is not None else _score_from_raw_json(raw_json, "confirmation_score")
    )
    independent_source_count = _coerce_int(
        independent_source_count if independent_source_count is not None else _score_from_raw_json(raw_json, "independent_source_count")
    )
    factuality_score = _coerce_float(
        factuality_score if factuality_score is not None else _score_from_raw_json(raw_json, "factuality_score")
    )
    evidence_score = _coerce_float(
        evidence_score if evidence_score is not None else _score_from_raw_json(raw_json, "evidence_score")
    )

    values = (
        source,
        article_key,
        title,
        normalized_title,
        summary,
        body,
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
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
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
           OR (content_hash != '' AND content_hash = ?)
           OR (normalized_title != '' AND normalized_title = ? AND published_at IS ?)
        ORDER BY id
        LIMIT 1
        """,
        (normalized_url, content_hash, normalized_title, published_at),
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
                body = ?,
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
                source_url = ?,
                raw_json = ?
            WHERE id = ?
            """,
            (
                source,
                article_key,
                title,
                normalized_title,
                summary,
                body,
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
                json_text(raw_json),
                article_id,
            ),
        )
        return article_id

    cursor = conn.execute(
        """
        INSERT INTO news_articles (
            source, article_key, title, normalized_title, summary, body,
            normalized_url, content_hash, published_at, section,
            age_days, recency_score, source_reputation_score, directness_score,
            confirmation_score, independent_source_count, factuality_score, evidence_score,
            source_url, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_key) DO UPDATE SET
            source = excluded.source,
            title = excluded.title,
            normalized_title = excluded.normalized_title,
            summary = excluded.summary,
            body = excluded.body,
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
            source_url = excluded.source_url,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def link_industry_to_article(
    industry_id: int,
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (industry_id, article_id, source_page_url)
    if conn is None:
        with get_connection(db_path) as local_conn:
            return link_industry_to_article(
                industry_id,
                article_id,
                source_page_url=source_page_url,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO industry_news_articles (industry_id, article_id, source_page_url)
        VALUES (?, ?, ?)
        ON CONFLICT(industry_id, article_id) DO UPDATE SET
            source_page_url = excluded.source_page_url
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_industry_news_article(
    industry_id: int,
    source: str,
    article_key: str,
    title: str,
    source_url: str,
    source_page_url: str | None = None,
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
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_industry_news_article(
                industry_id,
                source,
                article_key,
                title,
                source_url,
                source_page_url=source_page_url,
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

    article_id = add_news_article(
        source=source,
        article_key=article_key,
        title=title,
        source_url=source_url,
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
        conn=conn,
    )
    link_industry_to_article(
        industry_id=industry_id,
        article_id=article_id,
        source_page_url=source_page_url,
        conn=conn,
    )
    return article_id


def link_company_to_article(
    company_id: int,
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (company_id, article_id, source_page_url)
    if conn is None:
        with get_connection(db_path) as local_conn:
            return link_company_to_article(
                company_id,
                article_id,
                source_page_url=source_page_url,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO company_news_articles (company_id, article_id, source_page_url)
        VALUES (?, ?, ?)
        ON CONFLICT(company_id, article_id) DO UPDATE SET
            source_page_url = excluded.source_page_url
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def link_sector_to_article(
    sector_id: int,
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (sector_id, article_id, source_page_url)
    if conn is None:
        with get_connection(db_path) as local_conn:
            return link_sector_to_article(
                sector_id,
                article_id,
                source_page_url=source_page_url,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO sector_news_articles (sector_id, article_id, source_page_url)
        VALUES (?, ?, ?)
        ON CONFLICT(sector_id, article_id) DO UPDATE SET
            source_page_url = excluded.source_page_url
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def link_us_article(
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (article_id, source_page_url)
    if conn is None:
        with get_connection(db_path) as local_conn:
            return link_us_article(
                article_id,
                source_page_url=source_page_url,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO us_news_articles (article_id, source_page_url)
        VALUES (?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            source_page_url = excluded.source_page_url
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def link_world_article(
    article_id: int,
    source_page_url: str | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (article_id, source_page_url)
    if conn is None:
        with get_connection(db_path) as local_conn:
            return link_world_article(
                article_id,
                source_page_url=source_page_url,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO world_news_articles (article_id, source_page_url)
        VALUES (?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            source_page_url = excluded.source_page_url
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
    values = (
        article_id,
        sector_id,
        confidence,
        reason,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_world_news_sector_impact(
                article_id,
                sector_id,
                confidence=confidence,
                reason=reason,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO world_news_sector_impacts (
            article_id, sector_id, confidence, reason, raw_json
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(article_id, sector_id) DO UPDATE SET
            confidence = excluded.confidence,
            reason = excluded.reason,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_us_news_sector_impact(
    article_id: int,
    sector_id: int,
    confidence: str | None = None,
    reason: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        article_id,
        sector_id,
        confidence,
        reason,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_us_news_sector_impact(
                article_id,
                sector_id,
                confidence=confidence,
                reason=reason,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO us_news_sector_impacts (
            article_id, sector_id, confidence, reason, raw_json
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(article_id, sector_id) DO UPDATE SET
            confidence = excluded.confidence,
            reason = excluded.reason,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def mark_world_news_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        article_id,
        model,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return mark_world_news_article_processed(
                article_id,
                model=model,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO world_news_article_processing (article_id, model, raw_json)
        VALUES (?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            model = excluded.model,
            raw_json = excluded.raw_json,
            processed_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def mark_us_news_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        article_id,
        model,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return mark_us_news_article_processed(
                article_id,
                model=model,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO us_news_article_processing (article_id, model, raw_json)
        VALUES (?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            model = excluded.model,
            raw_json = excluded.raw_json,
            processed_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


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
    values = (
        article_id,
        industry_id,
        confidence,
        impact_direction,
        impact_magnitude,
        reason,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_industry_opportunist_impact(
                article_id,
                industry_id,
                confidence=confidence,
                impact_direction=impact_direction,
                impact_magnitude=impact_magnitude,
                reason=reason,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO industry_opportunist_impacts (
            article_id, industry_id, confidence, impact_direction, impact_magnitude, reason, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id, industry_id) DO UPDATE SET
            confidence = excluded.confidence,
            impact_direction = excluded.impact_direction,
            impact_magnitude = excluded.impact_magnitude,
            reason = excluded.reason,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def mark_industry_opportunist_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        article_id,
        model,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return mark_industry_opportunist_article_processed(
                article_id,
                model=model,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO industry_opportunist_article_processing (article_id, model, raw_json)
        VALUES (?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            model = excluded.model,
            raw_json = excluded.raw_json,
            processed_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


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
    values = (
        article_id,
        company_id,
        confidence,
        impact_direction,
        impact_magnitude,
        reason,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_company_opportunist_impact(
                article_id,
                company_id,
                confidence=confidence,
                impact_direction=impact_direction,
                impact_magnitude=impact_magnitude,
                reason=reason,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO company_opportunist_impacts (
            article_id, company_id, confidence, impact_direction, impact_magnitude, reason, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id, company_id) DO UPDATE SET
            confidence = excluded.confidence,
            impact_direction = excluded.impact_direction,
            impact_magnitude = excluded.impact_magnitude,
            reason = excluded.reason,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def mark_company_opportunist_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        article_id,
        model,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return mark_company_opportunist_article_processed(
                article_id,
                model=model,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO company_opportunist_article_processing (article_id, model, raw_json)
        VALUES (?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            model = excluded.model,
            raw_json = excluded.raw_json,
            processed_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


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
    values = (
        article_id,
        sector_id,
        confidence,
        impact_direction,
        impact_magnitude,
        reason,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_sector_opportunist_impact(
                article_id,
                sector_id,
                confidence=confidence,
                impact_direction=impact_direction,
                impact_magnitude=impact_magnitude,
                reason=reason,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO sector_opportunist_impacts (
            article_id, sector_id, confidence, impact_direction, impact_magnitude, reason, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id, sector_id) DO UPDATE SET
            confidence = excluded.confidence,
            impact_direction = excluded.impact_direction,
            impact_magnitude = excluded.impact_magnitude,
            reason = excluded.reason,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def mark_sector_opportunist_article_processed(
    article_id: int,
    model: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        article_id,
        model,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return mark_sector_opportunist_article_processed(
                article_id,
                model=model,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO sector_opportunist_article_processing (article_id, model, raw_json)
        VALUES (?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            model = excluded.model,
            raw_json = excluded.raw_json,
            processed_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_company_news_article(
    company_id: int,
    source: str,
    article_key: str,
    title: str,
    source_url: str,
    source_page_url: str | None = None,
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
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_company_news_article(
                company_id,
                source,
                article_key,
                title,
                source_url,
                source_page_url=source_page_url,
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

    article_id = add_news_article(
        source=source,
        article_key=article_key,
        title=title,
        source_url=source_url,
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
        conn=conn,
    )
    link_company_to_article(
        company_id=company_id,
        article_id=article_id,
        source_page_url=source_page_url,
        conn=conn,
    )
    return article_id


def add_sector_news_article(
    sector_id: int,
    source: str,
    article_key: str,
    title: str,
    source_url: str,
    source_page_url: str | None = None,
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
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_sector_news_article(
                sector_id,
                source,
                article_key,
                title,
                source_url,
                source_page_url=source_page_url,
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

    article_id = add_news_article(
        source=source,
        article_key=article_key,
        title=title,
        source_url=source_url,
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
        conn=conn,
    )
    link_sector_to_article(
        sector_id=sector_id,
        article_id=article_id,
        source_page_url=source_page_url,
        conn=conn,
    )
    return article_id


def add_us_news_article(
    source: str,
    article_key: str,
    title: str,
    source_url: str,
    source_page_url: str | None = None,
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
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_us_news_article(
                source,
                article_key,
                title,
                source_url,
                source_page_url=source_page_url,
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

    article_id = add_news_article(
        source=source,
        article_key=article_key,
        title=title,
        source_url=source_url,
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
        conn=conn,
    )
    link_us_article(
        article_id=article_id,
        source_page_url=source_page_url,
        conn=conn,
    )
    return article_id


def add_world_news_article(
    source: str,
    article_key: str,
    title: str,
    source_url: str,
    source_page_url: str | None = None,
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
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_world_news_article(
                source,
                article_key,
                title,
                source_url,
                source_page_url=source_page_url,
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

    article_id = add_news_article(
        source=source,
        article_key=article_key,
        title=title,
        source_url=source_url,
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
        conn=conn,
    )
    link_world_article(
        article_id=article_id,
        source_page_url=source_page_url,
        conn=conn,
    )
    return article_id


def load_macro_events(
    events: list[dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        for event in events:
            event_key = clean_text(event.get("event_key"))
            event_name = clean_text(event.get("event_name"))
            if event_key is None or event_name is None:
                continue

            add_macro_event(
                source=clean_text(event.get("source")) or "unknown",
                event_key=event_key,
                event_name=event_name,
                event_date=clean_text(event.get("event_date")),
                event_time=clean_text(event.get("event_time")),
                country=clean_text(event.get("country")),
                currency=clean_text(event.get("currency")),
                category=clean_text(event.get("category")),
                importance=clean_text(event.get("importance")),
                actual=clean_text(event.get("actual")),
                forecast=clean_text(event.get("forecast")),
                previous=clean_text(event.get("previous")),
                source_url=clean_text(event.get("source_url")),
                raw_json=event,
                conn=conn,
            )


def load_news_articles(
    articles: list[dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        for article in articles:
            source = clean_text(article.get("source")) or "unknown"
            article_key = clean_text(article.get("article_key")) or clean_text(article.get("source_url"))
            title = clean_text(article.get("title")) or article_key
            source_url = clean_text(article.get("source_url"))
            if article_key is None or title is None or source_url is None:
                continue

            add_news_article(
                source=source,
                article_key=article_key,
                title=title,
                source_url=source_url,
                summary=clean_text(article.get("summary")),
                body=clean_text(article.get("body")),
                published_at=clean_text(article.get("published_at")),
                section=clean_text(article.get("section")),
                raw_json=article,
                conn=conn,
            )


def list_industry_news_articles(industry_id: int, db_path: Path | str = DB_PATH) -> list[dict]:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                na.id,
                na.source,
                na.article_key,
                na.title,
                na.summary,
                na.body,
                na.published_at,
                na.section,
                na.source_url,
                ina.source_page_url
            FROM industry_news_articles AS ina
            JOIN news_articles AS na ON na.id = ina.article_id
            WHERE ina.industry_id = ?
            ORDER BY na.created_at DESC, na.id DESC
            """,
            (industry_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_company_news_articles(company_id: int, db_path: Path | str = DB_PATH) -> list[dict]:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                na.id,
                na.source,
                na.article_key,
                na.title,
                na.summary,
                na.body,
                na.published_at,
                na.section,
                na.source_url,
                cna.source_page_url
            FROM company_news_articles AS cna
            JOIN news_articles AS na ON na.id = cna.article_id
            WHERE cna.company_id = ?
            ORDER BY na.created_at DESC, na.id DESC
            """,
            (company_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_failed_url(url: str, db_path: Path | str = DB_PATH) -> dict | None:
    normalized_url = normalize_url(url)
    if not normalized_url:
        return None

    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
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
            WHERE normalized_url = ?
            LIMIT 1
            """,
            (normalized_url,),
        ).fetchone()
    return dict(row) if row is not None else None


def add_failed_url(
    url: str,
    stage: str | None = None,
    error: str | None = None,
    is_permanent: bool = False,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    normalized_url = normalize_url(url)
    if not normalized_url:
        return 0

    values = (
        url,
        normalized_url,
        stage,
        error,
        1 if is_permanent else 0,
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
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


def remove_failed_url(url: str, db_path: Path | str = DB_PATH, conn=None) -> None:
    normalized_url = normalize_url(url)
    if not normalized_url:
        return

    if conn is None:
        with get_connection(db_path) as local_conn:
            remove_failed_url(url, conn=local_conn)
            return

    conn.execute(
        """
        DELETE FROM failed_urls
        WHERE normalized_url = ?
        """,
        (normalized_url,),
    )


if __name__ == "__main__":
    initialize_database()
    print(f"Initialized news database schema in {DB_PATH}")
