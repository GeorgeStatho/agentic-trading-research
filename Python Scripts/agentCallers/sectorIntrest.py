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

from _shared import normalize_time_window, published_at_in_window
from db_helpers import DB_PATH, get_connection, initialize_news_database


def get_processed_sector_interest(
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = None,
) -> list[dict[str, Any]]:
    initialize_news_database()
    normalized_start, normalized_end = normalize_time_window(
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
                combined.sector_id,
                combined.confidence,
                combined.reason,
                combined.impact_created_at,
                combined.sector_key,
                combined.sector_name,
                combined.published_at,
                combined.processed_at,
                combined.model
            FROM (
                SELECT
                    'world' AS news_scope,
                    wsi.article_id,
                    wsi.sector_id,
                    wsi.confidence,
                    wsi.reason,
                    wsi.created_at AS impact_created_at,
                    s.sector_key,
                    s.name AS sector_name,
                    na.published_at,
                    wnap.processed_at,
                    wnap.model
                FROM world_news_sector_impacts AS wsi
                JOIN sectors AS s ON s.id = wsi.sector_id
                JOIN news_articles AS na ON na.id = wsi.article_id
                LEFT JOIN world_news_article_processing AS wnap ON wnap.article_id = wsi.article_id

                UNION ALL

                SELECT
                    'us' AS news_scope,
                    usi.article_id,
                    usi.sector_id,
                    usi.confidence,
                    usi.reason,
                    usi.created_at AS impact_created_at,
                    s.sector_key,
                    s.name AS sector_name,
                    na.published_at,
                    unap.processed_at,
                    unap.model
                FROM us_news_sector_impacts AS usi
                JOIN sectors AS s ON s.id = usi.sector_id
                JOIN news_articles AS na ON na.id = usi.article_id
                LEFT JOIN us_news_article_processing AS unap ON unap.article_id = usi.article_id
            ) AS combined
            ORDER BY combined.published_at DESC, combined.article_id DESC, combined.sector_id ASC
            """
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
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

if __name__ == "__main__":
    print(getTopThreeSectors(getSectorScores()))
