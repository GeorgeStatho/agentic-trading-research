from __future__ import annotations

"""Persistence and normalization helpers for the industry opportunist stage."""

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
    add_industry_opportunist_impact,
    get_all_industries,
    get_connection,
    initialize_news_database,
    mark_industry_opportunist_article_processed,
)
from db_helpers.market import ensure_sector_market_data
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
    "build_empty_result",
    "build_industry_opportunist_articles",
    "build_valid_reference_sets",
    "extract_impacts",
    "get_sector_industry_reference",
    "normalize_impact",
    "save_industry_opportunist_batch_results",
]


def get_sector_industry_reference(sector_identifier: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load the sector plus the industries that are eligible for it."""
    ensure_sector_market_data(sector_identifier)
    payload = build_opportunist_input(sector_identifier, max_age_days=None)
    sector = payload["sector"]

    industries = [
        {
            "industry_id": industry["id"],
            "industry_key": industry["industry_key"],
            "industry_name": industry["name"],
            "symbol": industry.get("symbol") or "",
        }
        for industry in get_all_industries()
        if str(industry.get("sector_key") or "").strip().lower()
        == str(sector.get("sector_key") or "").strip().lower()
    ]

    return sector, industries


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
        table_name="industry_opportunist_article_processing",
        required_impact_fields=SHARED_OPPORTUNIST_REQUIRED_IMPACT_FIELDS,
        minimum_schema_version=CURRENT_OPPORTUNIST_SCHEMA_VERSION,
    )


def build_industry_opportunist_articles(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Assemble the industry opportunist article batch for one sector."""
    payload = _load_opportunist_payload(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    sector = payload["sector"]

    _, industries = get_sector_industry_reference(sector_identifier)

    deduped_articles = _seed_articles_from_sector_rss(payload)
    deduped_articles = _merge_macro_articles(deduped_articles, payload)
    articles = _sort_articles(deduped_articles)
    articles = _filter_unprocessed_articles(articles)

    return sector, industries, articles


def extract_impacts(payload: Any) -> list[dict[str, Any]] | None:
    """Extract the normalized impact list from a model response payload."""
    return extract_impacts_from_payload(payload)


def build_empty_result(sector: dict[str, Any], industries: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the empty industry-opportunist response shape."""
    return {
        "sector": sector,
        "industries": industries,
        "impacts": [],
    }


def build_valid_reference_sets(
    industries: list[dict[str, Any]],
    articles: list[dict[str, Any]],
) -> tuple[set[int], set[str], set[int]]:
    """Build validation sets for model-produced industry impacts."""
    valid_industry_ids = {industry["industry_id"] for industry in industries}
    valid_industry_keys = {industry["industry_key"] for industry in industries}
    valid_article_ids = {article["article_id"] for article in articles}
    return valid_industry_ids, valid_industry_keys, valid_article_ids


def normalize_impact(
    impact: dict[str, Any],
    *,
    source_article_id: int,
    valid_industry_ids: set[int],
    valid_industry_keys: set[str],
) -> dict[str, Any] | None:
    """Validate one model-produced industry impact before persistence."""
    try:
        industry_id = int(impact.get("industry_id"))
    except (TypeError, ValueError):
        return None

    industry_key = str(impact.get("industry_key") or "").strip()

    if int(source_article_id) <= 0:
        return None
    if industry_id not in valid_industry_ids:
        return None
    if industry_key not in valid_industry_keys:
        return None
    shared_fields = normalize_shared_opportunist_impact_fields(impact)
    if shared_fields is None:
        return None

    return {
        "article_id": int(source_article_id),
        "industry_id": industry_id,
        "industry_key": industry_key,
        **shared_fields,
    }


def save_industry_opportunist_batch_results(
    article_batch: list[dict[str, Any]],
    impacts: list[dict[str, Any]],
    *,
    valid_industry_ids: set[int],
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
        scoped_industry_ids = sorted({int(industry_id) for industry_id in valid_industry_ids})
        if article_ids and scoped_industry_ids:
            article_placeholders = ",".join("?" for _ in article_ids)
            industry_placeholders = ",".join("?" for _ in scoped_industry_ids)
            conn.execute(
                f"""
                DELETE FROM industry_opportunist_impacts
                WHERE article_id IN ({article_placeholders})
                  AND industry_id IN ({industry_placeholders})
                """,
                (*article_ids, *scoped_industry_ids),
            )

        for impact in impacts:
            add_industry_opportunist_impact(
                article_id=int(impact["article_id"]),
                industry_id=int(impact["industry_id"]),
                confidence=str(impact["confidence"]),
                impact_direction=str(impact["impact_direction"]),
                impact_magnitude=str(impact["impact_magnitude"]),
                reason=str(impact["reason"]),
                raw_json=impact,
                conn=conn,
            )

        for article in article_batch:
            article_id = int(article["article_id"])
            mark_industry_opportunist_article_processed(
                article_id=article_id,
                model=model,
                raw_json={
                    "article_id": article_id,
                    "schema_version": CURRENT_OPPORTUNIST_SCHEMA_VERSION,
                    "required_impact_fields": list(SHARED_OPPORTUNIST_REQUIRED_IMPACT_FIELDS),
                    "valid_industry_ids": scoped_industry_ids,
                    "impacts": batch_impacts_by_article.get(article_id, []),
                    "raw_response": raw_response,
                },
                conn=conn,
            )
