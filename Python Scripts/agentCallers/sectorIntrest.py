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
from db_helpers import DB_PATH, get_connection, initialize_news_database, get_all_sectors


def _normalize_window(
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


def get_processed_sector_interest(
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = None,
) -> list[dict[str, Any]]:
    initialize_news_database()
    normalized_start, normalized_end = _normalize_window(
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                wsi.article_id,
                wsi.sector_id,
                wsi.confidence,
                wsi.reason,
                wsi.created_at AS impact_created_at,
                s.sector_key,
                s.name AS sector_name,
                na.title,
                na.summary,
                na.body,
                na.source,
                na.source_url,
                na.published_at,
                wnap.processed_at,
                wnap.model
            FROM world_news_sector_impacts AS wsi
            JOIN sectors AS s ON s.id = wsi.sector_id
            JOIN news_articles AS na ON na.id = wsi.article_id
            LEFT JOIN world_news_article_processing AS wnap ON wnap.article_id = wsi.article_id
            ORDER BY na.published_at DESC, wsi.article_id DESC, wsi.sector_id ASC
            """
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        published_at = parse_published_at(row["published_at"])
        if normalized_start is not None and (published_at is None or published_at < normalized_start):
            continue
        if normalized_end is not None and (published_at is None or published_at > normalized_end):
            continue

        results.append(
            {
                "article_id": row["article_id"],
                "sector_id": row["sector_id"],
                "sector_key": row["sector_key"],
                "sector_name": row["sector_name"],
                "confidence": row["confidence"] or "",
                "reason": row["reason"] or "",
                "published_at": row["published_at"] or "",
                "processed_at": row["processed_at"] or "",
                "model": row["model"] or "",
                "impact_created_at": row["impact_created_at"] or "",
            }
        )

    return results



def getSectorScores()->dict[str,int]:
    sectorCount={}
    result=get_processed_sector_interest()
    for summary in result:
        if summary["confidence"]=="high":
            sectorCount[summary["sector_key"]] = sectorCount.get(summary["sector_key"], 0) + 1
    
    return sectorCount

def getTopThreeSectors(sectorScores: dict[str,int]):
    return sorted(
        sectorScores.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:3]


print(getTopThreeSectors(getSectorScores()))

