from __future__ import annotations

from typing import Any

from db_common import DATA_DIR


SCHEMA_PATH = DATA_DIR / "news_schema.sql"

MACRO_EVENT_COLUMN_TYPES = {
    "currency": "TEXT",
    "actual": "TEXT",
    "forecast": "TEXT",
    "previous": "TEXT",
    "scheduled_at_utc": "TEXT",
    "calendar_timezone": "TEXT",
    "calendar_refreshed_at": "TEXT",
}

NEWS_ARTICLE_COLUMN_TYPES = {
    "source_type": "TEXT",
    "primary_ticker": "TEXT",
    "catalyst_id": "TEXT",
    "root_event_id": "TEXT",
    "root_source_id": "TEXT",
    "syndication_parent_id": "INTEGER",
    "primary_source_url": "TEXT",
    "event_time": "TEXT",
    "first_seen_time": "TEXT",
    "content_updated_at": "TEXT",
    "story_id": "INTEGER",

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


def score_from_raw_json(raw_json: Any | None, key: str) -> Any | None:
    if not isinstance(raw_json, dict):
        return None
    scores = raw_json.get("scores")
    if not isinstance(scores, dict):
        return None
    return scores.get(key)


