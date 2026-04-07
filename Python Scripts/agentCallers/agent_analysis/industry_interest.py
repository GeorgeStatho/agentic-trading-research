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

from _opportunist_payload_helpers import find_sector
from _shared import normalize_time_window, published_at_in_window
from db_helpers import DB_PATH, get_connection, initialize_news_database


def get_processed_industry_interest(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = None,
) -> list[dict[str, Any]]:
    initialize_news_database()
    sector = find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    normalized_start, normalized_end = normalize_time_window(
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                ioi.article_id,
                ioi.industry_id,
                ioi.confidence,
                ioi.impact_direction,
                ioi.impact_magnitude,
                ioi.reason,
                ioi.created_at AS impact_created_at,
                i.industry_key,
                i.name AS industry_name,
                s.id AS sector_id,
                s.sector_key,
                s.name AS sector_name,
                na.published_at,
                iop.processed_at,
                iop.model
            FROM industry_opportunist_impacts AS ioi
            JOIN industries AS i ON i.id = ioi.industry_id
            JOIN sectors AS s ON s.id = i.sector_id
            JOIN news_articles AS na ON na.id = ioi.article_id
            LEFT JOIN industry_opportunist_article_processing AS iop ON iop.article_id = ioi.article_id
            WHERE s.id = ?
            ORDER BY na.published_at DESC, ioi.article_id DESC, ioi.industry_id ASC
            """,
            (sector["id"],),
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
                "article_id": row["article_id"],
                "industry_id": row["industry_id"],
                "industry_key": row["industry_key"],
                "industry_name": row["industry_name"],
                "sector_id": row["sector_id"],
                "sector_key": row["sector_key"],
                "sector_name": row["sector_name"],
                "confidence": row["confidence"] or "",
                "impact_direction": row["impact_direction"] or "",
                "impact_magnitude": row["impact_magnitude"] or "",
                "reason": row["reason"] or "",
                "published_at": row["published_at"] or "",
                "processed_at": row["processed_at"] or "",
                "model": row["model"] or "",
                "impact_created_at": row["impact_created_at"] or "",
            }
        )

    return results


def getIndustryScores(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = None,
) -> dict[str, int]:
    industry_count: dict[str, int] = {}
    result = get_processed_industry_interest(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    for summary in result:
        if summary["confidence"] == "high":
            industry_count[summary["industry_key"]] = industry_count.get(summary["industry_key"], 0) + 1

    return industry_count


def getTopThreeIndustries(industry_scores: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(
        industry_scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:3]


if __name__ == "__main__":
    print(getTopThreeIndustries(getIndustryScores("technology")))
