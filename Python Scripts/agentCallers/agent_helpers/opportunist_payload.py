from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any

from agent_helpers.shared import normalize_time_window, published_at_in_window
from db_common import get_connection
from market_db_modules.queries import get_all_sectors
from news_db_modules.bootstrap import initialize_database as initialize_news_database


DEFAULT_MAX_ARTICLE_AGE_DAYS = 5
HIGH_CONFIDENCE = "high"
MEDIUM_CONFIDENCE = "medium"
ACCEPTED_CONFIDENCE_LEVELS = (HIGH_CONFIDENCE, MEDIUM_CONFIDENCE)
CNBC_SOURCE = "cnbc.com"

__all__ = [
    "ACCEPTED_CONFIDENCE_LEVELS",
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "HIGH_CONFIDENCE",
    "find_sector",
    "get_high_confidence_macro_news_for_sector",
    "get_sector_rss_news",
    "normalize_time_window",
]


def find_sector(sector_identifier: str) -> dict[str, Any] | None:
    needle = " ".join(str(sector_identifier or "").split()).strip().lower()
    if not needle:
        return None

    for sector in get_all_sectors():
        if str(sector.get("sector_key") or "").strip().lower() == needle:
            return sector
    for sector in get_all_sectors():
        if str(sector.get("name") or "").strip().lower() == needle:
            return sector
    return None

def _load_sector_rss_rows(sector_id: int) -> list[dict[str, Any]]:
    # Load the sector rss rows once so the downstream logic can stay focused on orchestration.
    with closing(get_connection()) as conn, conn:
        rows = conn.execute(
            """
            SELECT
                sna.article_id,
                sna.source_page_url,
                sna.created_at AS linked_at,
                na.title,
                na.summary,
                content.body,
                na.source,
                na.source_url,
                na.evidence_score,
                na.root_event_id,
                na.root_source_id,
                na.primary_ticker,
                na.catalyst_id,
                na.source_type,
                na.independent_source_count,
                na.published_at
            FROM sector_news_articles AS sna
            JOIN news_articles AS na ON na.id = sna.article_id
            LEFT JOIN news_article_content AS content ON content.article_id = na.id
            WHERE sna.sector_id = ?
              AND (
                  lower(coalesce(na.source, '')) = ?
                  OR lower(coalesce(na.source_url, '')) LIKE ?
                  OR lower(coalesce(sna.source_page_url, '')) LIKE ?
              )
            ORDER BY na.published_at DESC, sna.article_id DESC
            """,
            (
                sector_id,
                CNBC_SOURCE,
                "%cnbc.com%",
                "%cnbc.com%",
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def _row_to_sector_rss_article(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "article_id": row["article_id"],
        "title": row["title"] or "",
        "summary": row["summary"] or "",
        "body": row["body"] or "",
        "source": row["source"] or "",
        "source_url": row["source_url"] or "",
        "evidence_score": row.get("evidence_score"),
        "root_event_id": row.get("root_event_id"),
        "root_source_id": row.get("root_source_id"),
        "primary_ticker": row.get("primary_ticker"),
        "catalyst_id": row.get("catalyst_id"),
        "source_type": row.get("source_type"),
        "independent_source_count": row.get("independent_source_count"),
        "source_page_url": row["source_page_url"] or "",
        "published_at": row["published_at"] or "",
        "linked_at": row["linked_at"] or "",
    }


def get_sector_rss_news(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> list[dict[str, Any]]:
    # Load the sector rss news and normalize it for downstream callers.
    initialize_news_database()
    sector = find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    normalized_start, normalized_end = normalize_time_window(
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    rows = _load_sector_rss_rows(sector["id"])

    results: list[dict[str, Any]] = []
    for row in rows:
        row = dict(row)
        if not published_at_in_window(
            row["published_at"],
            start_time=normalized_start,
            end_time=normalized_end,
        ):
            continue
        results.append(_row_to_sector_rss_article(row))

    return results


def get_high_confidence_macro_news_for_sector(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> list[dict[str, Any]]:
    # Collect recent high-confidence macro articles for the target sector and package them for opportunist prompts.
    initialize_news_database()
    sector = find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    normalized_start, normalized_end = normalize_time_window(
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    with closing(get_connection()) as conn, conn:
        rows = conn.execute(
            """
            SELECT
                combined.news_scope,
                combined.article_id,
                combined.confidence,
                combined.reason,
                combined.impact_created_at,
                combined.processed_at,
                combined.model,
                combined.title,
                combined.summary,
                combined.body,
                combined.source,
                combined.source_url,
                combined.evidence_score,
                combined.root_event_id,
                combined.root_source_id,
                combined.primary_ticker,
                combined.catalyst_id,
                combined.source_type,
                combined.independent_source_count,
                combined.published_at
            FROM (
                SELECT
                    'world' AS news_scope,
                    wsi.article_id,
                    wsi.confidence,
                    wsi.reason,
                    wsi.created_at AS impact_created_at,
                    wnap.processed_at,
                    wnap.model,
                    na.title,
                    na.summary,
                    content.body,
                    na.source,
                    na.source_url,
                    na.evidence_score,
                    na.root_event_id,
                    na.root_source_id,
                    na.primary_ticker,
                    na.catalyst_id,
                    na.source_type,
                    na.independent_source_count,
                    na.published_at,
                    wsi.sector_id
                FROM world_news_sector_impacts AS wsi
                JOIN news_articles AS na ON na.id = wsi.article_id
                LEFT JOIN news_article_content AS content ON content.article_id = na.id
                LEFT JOIN world_news_article_processing AS wnap ON wnap.article_id = wsi.article_id

                UNION ALL

                SELECT
                    'us' AS news_scope,
                    usi.article_id,
                    usi.confidence,
                    usi.reason,
                    usi.created_at AS impact_created_at,
                    unap.processed_at,
                    unap.model,
                    na.title,
                    na.summary,
                    content.body,
                    na.source,
                    na.source_url,
                    na.evidence_score,
                    na.root_event_id,
                    na.root_source_id,
                    na.primary_ticker,
                    na.catalyst_id,
                    na.source_type,
                    na.independent_source_count,
                    na.published_at,
                    usi.sector_id
                FROM us_news_sector_impacts AS usi
                JOIN news_articles AS na ON na.id = usi.article_id
                LEFT JOIN news_article_content AS content ON content.article_id = na.id
                LEFT JOIN us_news_article_processing AS unap ON unap.article_id = usi.article_id
            ) AS combined
            WHERE combined.sector_id = ?
              AND lower(coalesce(combined.confidence, '')) IN (?, ?)
            ORDER BY combined.published_at DESC, combined.article_id DESC
            """,
            (sector["id"], *ACCEPTED_CONFIDENCE_LEVELS),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        row = dict(row)
        if not published_at_in_window(
            row["published_at"],
            start_time=normalized_start,
            end_time=normalized_end,
        ):
            continue

        results.append(
            {
                "news_scope": row["news_scope"],
                "article_id": row["article_id"],
                "confidence": row["confidence"] or "",
                "reason": row["reason"] or "",
                "impact_created_at": row["impact_created_at"] or "",
                "processed_at": row["processed_at"] or "",
                "model": row["model"] or "",
                "title": row["title"] or "",
                "summary": row["summary"] or "",
                "body": row["body"] or "",
                "source": row["source"] or "",
                "source_url": row["source_url"] or "",
                "evidence_score": row.get("evidence_score"),
                "root_event_id": row.get("root_event_id"),
                "root_source_id": row.get("root_source_id"),
                "primary_ticker": row.get("primary_ticker"),
                "catalyst_id": row.get("catalyst_id"),
                "source_type": row.get("source_type"),
                "independent_source_count": row.get("independent_source_count"),
                "published_at": row["published_at"] or "",
            }
        )

    return results
