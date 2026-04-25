from __future__ import annotations

"""Persistence and normalization helpers for the sector opportunist stage."""

from datetime import datetime
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers


bootstrap_agent_callers()

from OppurtunistPayloadBuilder import build_opportunist_input
from db_helpers import (
    DB_PATH,
    add_sector_opportunist_impact,
    get_connection,
    initialize_news_database,
    mark_sector_opportunist_article_processed,
)
from agent_helpers.opportunist_support import (
    CURRENT_OPPORTUNIST_SCHEMA_VERSION,
    SHARED_OPPORTUNIST_REQUIRED_IMPACT_FIELDS,
    build_base_article_record,
    extract_impacts_from_payload,
    filter_unprocessed_articles,
    merge_macro_articles,
    normalize_shared_opportunist_impact_fields,
    seed_sector_rss_articles,
    sort_articles_by_recency,
)

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
    """Load the canonical sector record used by the sector opportunist stage."""
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
    return build_base_article_record(article, article_scope=article_scope, sector_source=sector_source)


def _seed_articles_from_sector_rss(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return seed_sector_rss_articles(payload)


def _merge_macro_articles(
    deduped_articles: dict[int, dict[str, Any]],
    payload: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    return merge_macro_articles(deduped_articles, payload)


def _sort_articles(deduped_articles: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return sort_articles_by_recency(deduped_articles)


def _filter_unprocessed_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return filter_unprocessed_articles(
        articles,
        db_path=str(DB_PATH),
        table_name="sector_opportunist_article_processing",
        required_impact_fields=SHARED_OPPORTUNIST_REQUIRED_IMPACT_FIELDS,
        minimum_schema_version=CURRENT_OPPORTUNIST_SCHEMA_VERSION,
    )


def build_sector_opportunist_articles(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Assemble the sector opportunist article batch for one sector.

    Usage:
        Call this before sending work to ``agent_stages.sector_opportunist``.
    """
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


def extract_sector_impacts(payload: Any) -> list[dict[str, Any]] | None:
    """Extract the normalized impact list from a model response payload."""
    return extract_impacts_from_payload(payload)


def build_empty_sector_result(sector: dict[str, Any]) -> dict[str, Any]:
    """Return the empty sector-opportunist response shape."""
    return {
        "sector": sector,
        "impacts": [],
    }


def build_sector_valid_reference_sets(
    sector: dict[str, Any],
    articles: list[dict[str, Any]],
) -> tuple[int, str, set[int]]:
    """Build reference sets used to validate model-produced impacts."""
    valid_sector_id = int(sector["sector_id"])
    valid_sector_name = str(sector["sector_name"])
    valid_article_ids = {article["article_id"] for article in articles}
    return valid_sector_id, valid_sector_name, valid_article_ids


def normalize_sector_impact(
    impact: dict[str, Any],
    *,
    source_article_id: int,
    valid_sector_id: int,
    valid_sector_name: str,
) -> dict[str, Any] | None:
    """Validate one model-produced sector impact before persistence."""
    if int(source_article_id) <= 0:
        return None
    shared_fields = normalize_shared_opportunist_impact_fields(impact)
    if shared_fields is None:
        return None

    return {
        "article_id": int(source_article_id),
        "sector_id": int(valid_sector_id),
        "sector_name": str(valid_sector_name),
        **shared_fields,
    }


def save_sector_opportunist_batch_results(
    article_batch: list[dict[str, Any]],
    impacts: list[dict[str, Any]],
    *,
    sector_id: int,
    model: str,
    raw_response: str,
) -> None:
    """Persist the cleaned impacts and mark the article batch as processed."""
    initialize_news_database()
    batch_impacts_by_article: dict[int, list[dict[str, Any]]] = {}
    for impact in impacts:
        article_id = int(impact["article_id"])
        batch_impacts_by_article.setdefault(article_id, []).append(impact)

    with get_connection(DB_PATH) as conn:
        article_ids = [int(article["article_id"]) for article in article_batch]
        if article_ids:
            placeholders = ",".join("?" for _ in article_ids)
            conn.execute(
                f"""
                DELETE FROM sector_opportunist_impacts
                WHERE sector_id = ?
                  AND article_id IN ({placeholders})
                """,
                (int(sector_id), *article_ids),
            )

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
                    "sector_id": int(sector_id),
                    "schema_version": CURRENT_OPPORTUNIST_SCHEMA_VERSION,
                    "required_impact_fields": list(SHARED_OPPORTUNIST_REQUIRED_IMPACT_FIELDS),
                    "impacts": batch_impacts_by_article.get(article_id, []),
                    "raw_response": raw_response,
                },
                conn=conn,
            )
