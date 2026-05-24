from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from db_common import DATA_DIR

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


def score_from_raw_json(raw_json: Any | None, key: str) -> Any | None:
    if not isinstance(raw_json, dict):
        return None
    scores = raw_json.get("scores")
    if not isinstance(scores, dict):
        return None
    return scores.get(key)


def coerce_float(value: Any | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_int(value: Any | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
