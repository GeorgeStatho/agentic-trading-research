from __future__ import annotations

from datetime import datetime, timedelta, timezone
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

from _shared import parse_published_at
from db_helpers import DB_PATH, get_all_sectors, get_connection, initialize_news_database


DEFAULT_MAX_ARTICLE_AGE_DAYS = 3
HIGH_CONFIDENCE = "high"
CNBC_SOURCE = "cnbc.com"

__all__ = [
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "HIGH_CONFIDENCE",
    "find_sector",
    "get_high_confidence_macro_news_for_sector",
    "get_sector_rss_news",
    "normalize_window",
]


def normalize_window(
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
) -> tuple[datetime | None, datetime | None]:
    normalized_end = end_time.astimezone(timezone.utc) if end_time is not None else datetime.now(timezone.utc)
    normalized_start = start_time.astimezone(timezone.utc) if start_time is not None else None

    if normalized_start is None and max_age_days is not None:
        normalized_start = normalized_end - timedelta(days=max_age_days)

    return normalized_start, normalized_end


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


def _in_window(
    published_at_text: str | None,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
) -> bool:
    published_at = parse_published_at(published_at_text)
    if published_at is None:
        return False
    if start_time is not None and published_at < start_time:
        return False
    if end_time is not None and published_at > end_time:
        return False
    return True


def _load_sector_rss_rows(sector_id: int) -> list[dict[str, Any]]:
    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                sna.article_id,
                sna.source_page_url,
                sna.created_at AS linked_at,
                na.title,
                na.summary,
                na.body,
                na.source,
                na.source_url,
                na.published_at
            FROM sector_news_articles AS sna
            JOIN news_articles AS na ON na.id = sna.article_id
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
    initialize_news_database()
    sector = find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    normalized_start, normalized_end = normalize_window(
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    rows = _load_sector_rss_rows(sector["id"])

    results: list[dict[str, Any]] = []
    for row in rows:
        if not _in_window(
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
    initialize_news_database()
    sector = find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    normalized_start, normalized_end = normalize_window(
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    with get_connection(DB_PATH) as conn:
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
                    na.body,
                    na.source,
                    na.source_url,
                    na.published_at,
                    wsi.sector_id
                FROM world_news_sector_impacts AS wsi
                JOIN news_articles AS na ON na.id = wsi.article_id
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
                    na.body,
                    na.source,
                    na.source_url,
                    na.published_at,
                    usi.sector_id
                FROM us_news_sector_impacts AS usi
                JOIN news_articles AS na ON na.id = usi.article_id
                LEFT JOIN us_news_article_processing AS unap ON unap.article_id = usi.article_id
            ) AS combined
            WHERE combined.sector_id = ?
              AND lower(coalesce(combined.confidence, '')) = ?
            ORDER BY combined.published_at DESC, combined.article_id DESC
            """,
            (sector["id"], HIGH_CONFIDENCE),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        if not _in_window(
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
                "published_at": row["published_at"] or "",
            }
        )

    return results
