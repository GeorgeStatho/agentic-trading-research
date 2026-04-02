from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Any


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from _company_opportunist_helpers import get_company_reference
from _opportunist_payload_helpers import (
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    HIGH_CONFIDENCE,
    get_high_confidence_macro_news_for_sector,
    get_sector_rss_news,
)
from _shared import normalize_time_window, published_at_in_window
from db_helpers import DB_PATH, get_connection, initialize_news_database


__all__ = [
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "HIGH_CONFIDENCE",
    "build_strategist_evidence_sections",
    "get_company_context",
    "get_high_confidence_company_news",
    "get_high_confidence_industry_news",
    "get_macro_news_for_company_sector",
    "get_sector_news_for_company_sector",
]


def get_company_context(company_identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    company, peer_groups, _articles = get_company_reference(company_identifier)
    return company, peer_groups


def _build_processed_article_record(
    row: dict[str, Any],
    *,
    article_scope: str,
    subject_id_key: str,
    subject_key_key: str,
    subject_name_key: str,
) -> dict[str, Any]:
    return {
        "article_id": row["article_id"],
        subject_id_key: row[subject_id_key],
        subject_key_key: row[subject_key_key],
        subject_name_key: row[subject_name_key],
        "confidence": row["confidence"] or "",
        "impact_direction": row["impact_direction"] or "",
        "impact_magnitude": row["impact_magnitude"] or "",
        "reason": row["reason"] or "",
        "impact_created_at": row["impact_created_at"] or "",
        "processed_at": row["processed_at"] or "",
        "model": row["model"] or "",
        "title": row["title"] or "",
        "summary": row["summary"] or "",
        "body": row["body"] or "",
        "source": row["source"] or "",
        "source_url": row["source_url"] or "",
        "published_at": row["published_at"] or "",
        "article_scope": article_scope,
    }


def _filter_rows_to_window(
    rows: list[dict[str, Any]],
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
) -> list[dict[str, Any]]:
    normalized_start, normalized_end = normalize_time_window(
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    return [
        row
        for row in rows
        if published_at_in_window(
            row.get("published_at"),
            start_time=normalized_start,
            end_time=normalized_end,
        )
    ]


def get_macro_news_for_company_sector(
    company: dict[str, Any],
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> list[dict[str, Any]]:
    return get_high_confidence_macro_news_for_sector(
        company["sector_key"],
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )


def get_sector_news_for_company_sector(
    company: dict[str, Any],
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> list[dict[str, Any]]:
    return get_sector_rss_news(
        company["sector_key"],
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )


def _load_high_confidence_industry_rows(industry_id: int) -> list[dict[str, Any]]:
    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                ioi.article_id,
                ioi.industry_id,
                i.industry_key,
                i.name AS industry_name,
                ioi.confidence,
                ioi.impact_direction,
                ioi.impact_magnitude,
                ioi.reason,
                ioi.created_at AS impact_created_at,
                iop.processed_at,
                iop.model,
                na.title,
                na.summary,
                na.body,
                na.source,
                na.source_url,
                na.published_at
            FROM industry_opportunist_impacts AS ioi
            JOIN industries AS i ON i.id = ioi.industry_id
            JOIN news_articles AS na ON na.id = ioi.article_id
            LEFT JOIN industry_opportunist_article_processing AS iop ON iop.article_id = ioi.article_id
            WHERE ioi.industry_id = ?
              AND lower(coalesce(ioi.confidence, '')) = ?
            ORDER BY na.published_at DESC, ioi.article_id DESC
            """,
            (industry_id, HIGH_CONFIDENCE),
        ).fetchall()

    return [dict(row) for row in rows]


def get_high_confidence_industry_news(
    company: dict[str, Any],
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> list[dict[str, Any]]:
    rows = _load_high_confidence_industry_rows(int(company["industry_id"]))
    rows = _filter_rows_to_window(
        rows,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    return [
        _build_processed_article_record(
            row,
            article_scope="industry_news",
            subject_id_key="industry_id",
            subject_key_key="industry_key",
            subject_name_key="industry_name",
        )
        for row in rows
    ]


def _load_high_confidence_company_rows(company_id: int) -> list[dict[str, Any]]:
    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                coi.article_id,
                coi.company_id,
                c.symbol,
                c.name AS company_name,
                coi.confidence,
                coi.impact_direction,
                coi.impact_magnitude,
                coi.reason,
                coi.created_at AS impact_created_at,
                cop.processed_at,
                cop.model,
                na.title,
                na.summary,
                na.body,
                na.source,
                na.source_url,
                na.published_at
            FROM company_opportunist_impacts AS coi
            JOIN companies AS c ON c.id = coi.company_id
            JOIN news_articles AS na ON na.id = coi.article_id
            LEFT JOIN company_opportunist_article_processing AS cop ON cop.article_id = coi.article_id
            WHERE coi.company_id = ?
              AND lower(coalesce(coi.confidence, '')) = ?
            ORDER BY na.published_at DESC, coi.article_id DESC
            """,
            (company_id, HIGH_CONFIDENCE),
        ).fetchall()

    return [dict(row) for row in rows]


def get_high_confidence_company_news(
    company: dict[str, Any],
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> list[dict[str, Any]]:
    rows = _load_high_confidence_company_rows(int(company["company_id"]))
    rows = _filter_rows_to_window(
        rows,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    return [
        _build_processed_article_record(
            row,
            article_scope="company_news",
            subject_id_key="company_id",
            subject_key_key="symbol",
            subject_name_key="company_name",
        )
        for row in rows
    ]


def build_strategist_evidence_sections(
    company_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> dict[str, Any]:
    initialize_news_database()
    company, peer_groups = get_company_context(company_identifier)

    return {
        "company": company,
        "peer_groups": peer_groups,
        "macro_news": get_macro_news_for_company_sector(
            company,
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
        ),
        "sector_news": get_sector_news_for_company_sector(
            company,
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
        ),
        "industry_news": get_high_confidence_industry_news(
            company,
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
        ),
        "company_news": get_high_confidence_company_news(
            company,
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
        ),
    }
