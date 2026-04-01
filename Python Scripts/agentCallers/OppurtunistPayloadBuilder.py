from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from _opportunist_payload_helpers import (
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    HIGH_CONFIDENCE,
    find_sector,
    get_high_confidence_macro_news_for_sector,
    get_sector_rss_news,
)
from db_helpers import initialize_news_database


__all__ = [
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "build_opportunist_input",
    "build_opportunist_prompt_payload",
]


def _build_payload_filters(
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
) -> dict[str, str | int | None]:
    return {
        "start_time": start_time.astimezone(timezone.utc).isoformat() if start_time is not None else "",
        "end_time": end_time.astimezone(timezone.utc).isoformat() if end_time is not None else "",
        "max_age_days": max_age_days,
        "macro_confidence": HIGH_CONFIDENCE,
        "sector_news_source": "cnbc_rss",
    }


def _build_payload_sector(sector: dict) -> dict[str, int | str]:
    return {
        "sector_id": sector["id"],
        "sector_key": sector["sector_key"],
        "sector_name": sector["name"],
    }


def build_opportunist_input(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> dict:
    initialize_news_database()
    sector = find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    return {
        "sector": _build_payload_sector(sector),
        "filters": _build_payload_filters(
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
        ),
        "sector_rss_news": get_sector_rss_news(
            sector_identifier,
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
        ),
        "related_macro_news": get_high_confidence_macro_news_for_sector(
            sector_identifier,
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
        ),
    }


def build_opportunist_prompt_payload(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> str:
    payload = build_opportunist_input(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    return json.dumps(payload, ensure_ascii=True, indent=2)


if __name__ == "__main__":
    print(build_opportunist_prompt_payload("technology"))
