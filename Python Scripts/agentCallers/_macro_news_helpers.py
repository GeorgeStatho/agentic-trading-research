from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Callable


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from _shared import parse_published_at
from db_helpers import DB_PATH, get_all_sectors, get_connection
from db_helpers import (
    add_us_news_sector_impact,
    add_world_news_sector_impact,
    mark_us_news_article_processed,
    mark_world_news_article_processed,
)


ImpactSaver = Callable[..., int]
ProcessedMarker = Callable[..., int]

SCOPE_CONFIG: dict[str, dict[str, Any]] = {
    "world": {
        "label": "world",
        "membership_table": "world_news_articles",
        "processing_table": "world_news_article_processing",
        "impact_saver": add_world_news_sector_impact,
        "processed_marker": mark_world_news_article_processed,
    },
    "us": {
        "label": "U.S.",
        "membership_table": "us_news_articles",
        "processing_table": "us_news_article_processing",
        "impact_saver": add_us_news_sector_impact,
        "processed_marker": mark_us_news_article_processed,
    },
}


def get_scope_config(news_scope: str) -> dict[str, Any]:
    normalized_scope = str(news_scope or "").strip().lower()
    config = SCOPE_CONFIG.get(normalized_scope)
    if config is None:
        raise ValueError(f"Unsupported news scope: {news_scope!r}")
    return config


def _load_unprocessed_macro_news_rows(news_scope: str) -> list[dict[str, Any]]:
    config = get_scope_config(news_scope)
    membership_table = config["membership_table"]
    processing_table = config["processing_table"]

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT
                na.id AS article_id,
                na.title,
                na.body,
                na.summary,
                na.source,
                na.source_url,
                na.published_at
            FROM {membership_table} AS mna
            JOIN news_articles AS na ON na.id = mna.article_id
            LEFT JOIN {processing_table} AS pna ON pna.article_id = na.id
            WHERE pna.article_id IS NULL
            ORDER BY na.published_at DESC, na.id DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def _row_to_macro_article(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "article_id": row["article_id"],
        "title": row["title"] or "",
        "body": row["body"] or "",
        "summary": row["summary"] or "",
        "source": row["source"] or "",
        "source_url": row["source_url"] or "",
        "published_at": row["published_at"] or "",
    }


def _is_recent_enough(row: dict[str, Any], *, cutoff: datetime, max_age_days: int) -> bool:
    published_at = parse_published_at(row.get("published_at"))
    if published_at is None:
        return False

    age_days = (cutoff - published_at).total_seconds() / 86400
    return age_days <= max_age_days


def get_recent_macro_news_articles(
    news_scope: str,
    *,
    max_age_days: int,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc)
    rows = _load_unprocessed_macro_news_rows(news_scope)

    articles: list[dict[str, Any]] = []
    for row in rows:
        if not _is_recent_enough(row, cutoff=cutoff, max_age_days=max_age_days):
            continue
        articles.append(_row_to_macro_article(row))

    return articles


def get_sector_reference() -> list[dict[str, Any]]:
    return [
        {
            "sector_id": sector["id"],
            "sector_key": sector["sector_key"],
            "sector_name": sector["name"],
        }
        for sector in get_all_sectors()
    ]


def save_batch_results(
    article_batch: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    *,
    model: str,
    raw_response: str,
    news_scope: str,
) -> None:
    config = get_scope_config(news_scope)
    impact_saver: ImpactSaver = config["impact_saver"]
    processed_marker: ProcessedMarker = config["processed_marker"]

    batch_pairs_by_article: dict[int, list[dict[str, Any]]] = {}
    for pair in pairs:
        article_id = int(pair["article_id"])
        batch_pairs_by_article.setdefault(article_id, []).append(pair)

    with get_connection(DB_PATH) as conn:
        for pair in pairs:
            impact_saver(
                article_id=int(pair["article_id"]),
                sector_id=int(pair["sector_id"]),
                confidence=str(pair["confidence"]),
                reason=str(pair["reason"]),
                raw_json=pair,
                conn=conn,
            )

        for article in article_batch:
            article_id = int(article["article_id"])
            processed_marker(
                article_id=article_id,
                model=model,
                raw_json={
                    "news_scope": news_scope,
                    "article_id": article_id,
                    "pairs": batch_pairs_by_article.get(article_id, []),
                    "raw_response": raw_response,
                },
                conn=conn,
            )
