from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from OppurtunistPayloadBuilder import build_opportunist_input
from db_helpers import (
    DB_PATH,
    add_sector_opportunist_impact,
    get_connection,
    initialize_news_database,
    mark_sector_opportunist_article_processed,
)


VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}
VALID_IMPACT_DIRECTIONS = {"positive", "negative"}
VALID_IMPACT_MAGNITUDES = {"major", "moderate", "modest"}

__all__ = [
    "build_empty_sector_result",
    "build_sector_opportunist_articles",
    "build_sector_valid_reference_sets",
    "extract_sector_impacts",
    "get_sector_reference",
    "normalize_sector_impact",
    "save_sector_opportunist_batch_results",
]


def get_sector_reference(sector_identifier: str) -> dict[str, Any]:
    payload = build_opportunist_input(sector_identifier, max_age_days=None)
    return payload["sector"]


def _load_opportunist_payload(
    sector_identifier: str,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
) -> dict[str, Any]:
    return build_opportunist_input(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )


def _make_base_article_record(article: dict[str, Any], *, article_scope: str, sector_source: str) -> dict[str, Any]:
    return {
        "article_id": int(article["article_id"]),
        "title": article.get("title") or "",
        "summary": article.get("summary") or "",
        "body": article.get("body") or "",
        "source": article.get("source") or "",
        "source_url": article.get("source_url") or "",
        "published_at": article.get("published_at") or "",
        "article_scope": article_scope,
        "sector_context": {
            "sector_source": sector_source,
        },
    }


def _seed_articles_from_sector_rss(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    deduped_articles: dict[int, dict[str, Any]] = {}

    for article in payload.get("sector_rss_news", []):
        article_record = _make_base_article_record(
            article,
            article_scope="sector_rss",
            sector_source="cnbc_rss",
        )
        deduped_articles[article_record["article_id"]] = article_record

    return deduped_articles


def _merge_macro_articles(
    deduped_articles: dict[int, dict[str, Any]],
    payload: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    for article in payload.get("related_macro_news", []):
        article_id = int(article["article_id"])
        entry = deduped_articles.setdefault(
            article_id,
            _make_base_article_record(
                article,
                article_scope="macro_news",
                sector_source="macro_news_high_confidence",
            ),
        )
        entry["macro_context"] = {
            "news_scope": article.get("news_scope") or "",
            "confidence": article.get("confidence") or "",
            "reason": article.get("reason") or "",
        }
        if entry.get("article_scope") == "sector_rss":
            entry["article_scope"] = "sector_rss_and_macro_news"

    return deduped_articles


def _sort_articles(deduped_articles: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        deduped_articles.values(),
        key=lambda article: (
            str(article.get("published_at") or ""),
            int(article.get("article_id") or 0),
        ),
        reverse=True,
    )


def _load_processed_article_ids(article_ids: list[int]) -> set[int]:
    if not article_ids:
        return set()

    placeholders = ",".join("?" for _ in article_ids)
    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT article_id
            FROM sector_opportunist_article_processing
            WHERE article_id IN ({placeholders})
            """,
            tuple(article_ids),
        ).fetchall()

    return {int(row["article_id"]) for row in rows}


def _filter_unprocessed_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    processed_article_ids = _load_processed_article_ids(
        [int(article["article_id"]) for article in articles]
    )
    if not processed_article_ids:
        return articles

    return [
        article
        for article in articles
        if int(article["article_id"]) not in processed_article_ids
    ]


def build_sector_opportunist_articles(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _load_opportunist_payload(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    sector = payload["sector"]

    deduped_articles = _seed_articles_from_sector_rss(payload)
    deduped_articles = _merge_macro_articles(deduped_articles, payload)
    articles = _sort_articles(deduped_articles)
    articles = _filter_unprocessed_articles(articles)

    return sector, articles


def extract_sector_impacts(payload: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None

    impacts = payload.get("impacts")
    if isinstance(impacts, list):
        return impacts

    for key in ("output_schema", "required_output"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_impacts = nested.get("impacts")
            if isinstance(nested_impacts, list):
                return nested_impacts

    return None


def build_empty_sector_result(sector: dict[str, Any]) -> dict[str, Any]:
    return {
        "sector": sector,
        "impacts": [],
    }


def build_sector_valid_reference_sets(
    sector: dict[str, Any],
    articles: list[dict[str, Any]],
) -> tuple[int, str, set[int]]:
    valid_sector_id = int(sector["sector_id"])
    valid_sector_name = str(sector["sector_name"])
    valid_article_ids = {article["article_id"] for article in articles}
    return valid_sector_id, valid_sector_name, valid_article_ids


def normalize_sector_impact(
    impact: dict[str, Any],
    *,
    valid_article_ids: set[int],
    valid_sector_id: int,
    valid_sector_name: str,
) -> dict[str, Any] | None:
    try:
        article_id = int(impact.get("article_id"))
        sector_id = int(impact.get("sector_id"))
    except (TypeError, ValueError):
        return None

    sector_name = str(impact.get("sector_name") or "").strip()
    confidence = str(impact.get("confidence") or "").strip().lower()
    impact_direction = str(impact.get("impact_direction") or "").strip().lower()
    impact_magnitude = str(impact.get("impact_magnitude") or "").strip().lower()
    reason = str(impact.get("reason") or "").strip()

    if article_id not in valid_article_ids:
        return None
    if sector_id != valid_sector_id:
        return None
    if sector_name != valid_sector_name:
        return None
    if confidence not in VALID_CONFIDENCE_LEVELS:
        return None
    if impact_direction not in VALID_IMPACT_DIRECTIONS:
        return None
    if impact_magnitude not in VALID_IMPACT_MAGNITUDES:
        return None
    if not reason:
        return None

    return {
        "article_id": article_id,
        "sector_id": sector_id,
        "sector_name": sector_name,
        "confidence": confidence,
        "impact_direction": impact_direction,
        "impact_magnitude": impact_magnitude,
        "reason": reason,
    }


def save_sector_opportunist_batch_results(
    article_batch: list[dict[str, Any]],
    impacts: list[dict[str, Any]],
    *,
    model: str,
    raw_response: str,
) -> None:
    initialize_news_database()
    batch_impacts_by_article: dict[int, list[dict[str, Any]]] = {}
    for impact in impacts:
        article_id = int(impact["article_id"])
        batch_impacts_by_article.setdefault(article_id, []).append(impact)

    with get_connection(DB_PATH) as conn:
        for impact in impacts:
            add_sector_opportunist_impact(
                article_id=int(impact["article_id"]),
                sector_id=int(impact["sector_id"]),
                confidence=str(impact["confidence"]),
                impact_direction=str(impact["impact_direction"]),
                impact_magnitude=str(impact["impact_magnitude"]),
                reason=str(impact["reason"]),
                raw_json=impact,
                conn=conn,
            )

        for article in article_batch:
            article_id = int(article["article_id"])
            mark_sector_opportunist_article_processed(
                article_id=article_id,
                model=model,
                raw_json={
                    "article_id": article_id,
                    "impacts": batch_impacts_by_article.get(article_id, []),
                    "raw_response": raw_response,
                },
                conn=conn,
            )
