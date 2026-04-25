from __future__ import annotations

"""Persistence and normalization helpers for the company opportunist stage."""

from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers


bootstrap_agent_callers()

from CompanyOppurtunityBuilder import get_company_linked_articles, get_industry_company_groups
from db_helpers import (
    DB_PATH,
    add_company_opportunist_impact,
    get_connection,
    initialize_news_database,
    mark_company_opportunist_article_processed,
)
from agent_helpers.opportunist_support import (
    extract_impacts_from_payload,
    filter_unprocessed_articles,
    normalize_shared_opportunist_impact_fields,
    sort_articles_by_recency,
)


VALID_RELATIVE_POSITIONING = {
    "better_than_peers",
    "worse_than_peers",
    "similar",
    "not_applicable",
}

__all__ = [
    "build_company_opportunist_articles",
    "build_company_valid_reference_sets",
    "build_empty_company_result",
    "extract_company_impacts",
    "get_company_opportunist_summary",
    "get_company_reference",
    "normalize_company_impact",
    "save_company_opportunist_batch_results",
]


def get_company_reference(company_identifier: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Load the company, its peer groups, and its linked news articles."""
    company_payload = get_company_linked_articles(company_identifier)
    company = company_payload["company"]
    peer_groups = get_industry_company_groups(company["industry_key"])
    articles = company_payload["articles"]
    return company, peer_groups, articles


def get_company_opportunist_summary(
    company_identifier: str,
    *,
    sample_reason_limit: int = 3,
) -> dict[str, Any]:
    """Summarize the saved company opportunist impacts for one company."""
    company_payload = get_company_linked_articles(company_identifier)
    company = company_payload["company"]

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                confidence,
                impact_direction,
                impact_magnitude,
                reason,
                created_at
            FROM company_opportunist_impacts
            WHERE company_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (int(company["company_id"]),),
        ).fetchall()

    confidence_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    magnitude_counts: dict[str, int] = {}
    reasons: list[str] = []

    for row in rows:
        confidence = str(row["confidence"] or "").strip().lower()
        impact_direction = str(row["impact_direction"] or "").strip().lower()
        impact_magnitude = str(row["impact_magnitude"] or "").strip().lower()
        reason = str(row["reason"] or "").strip()

        if confidence:
            confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
        if impact_direction:
            direction_counts[impact_direction] = direction_counts.get(impact_direction, 0) + 1
        if impact_magnitude:
            magnitude_counts[impact_magnitude] = magnitude_counts.get(impact_magnitude, 0) + 1
        if reason and reason not in reasons:
            reasons.append(reason)

    return {
        "company": {
            "company_id": company.get("company_id"),
            "symbol": company.get("symbol"),
            "name": company.get("name"),
            "industry_key": company.get("industry_key"),
            "sector_key": company.get("sector_key"),
        },
        "impact_count": len(rows),
        "confidence_counts": confidence_counts,
        "direction_counts": direction_counts,
        "magnitude_counts": magnitude_counts,
        "sample_reasons": reasons[: max(0, int(sample_reason_limit))],
    }


def _make_company_article_record(article: dict[str, Any]) -> dict[str, Any]:
    return {
        "article_id": int(article["id"]),
        "title": article.get("title") or "",
        "summary": article.get("summary") or "",
        "body": article.get("body") or "",
        "source": article.get("source") or "",
        "source_url": article.get("source_url") or "",
        "source_page_url": article.get("source_page_url") or "",
        "published_at": article.get("published_at") or "",
        "article_scope": "company_news",
    }


def _sort_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sort_articles_by_recency(articles)


def _filter_unprocessed_articles(articles: list[dict[str, Any]], company_id: int) -> list[dict[str, Any]]:
    return filter_unprocessed_articles(
        articles,
        db_path=str(DB_PATH),
        table_name="company_opportunist_article_processing",
        company_id=company_id,
    )


def build_company_opportunist_articles(
    company_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Assemble the company opportunist article batch for one company."""
    company, peer_groups, raw_articles = get_company_reference(company_identifier)

    articles = [_make_company_article_record(article) for article in raw_articles]
    articles = _sort_articles(articles)
    articles = _filter_unprocessed_articles(articles, int(company["company_id"]))

    return company, peer_groups, articles


def _parse_company_payload(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    fence_start = raw.find("```")
    if fence_start >= 0:
        fence_end = raw.rfind("```")
        if fence_end > fence_start:
            fenced = raw[fence_start + 3:fence_end].strip()
            if fenced.lower().startswith("json"):
                fenced = fenced[4:].strip()
            try:
                return json.loads(fenced)
            except json.JSONDecodeError:
                pass

    for open_char, close_char in (("[", "]"), ("{", "}")):
        candidate_start = raw.find(open_char)
        candidate_end = raw.rfind(close_char)
        if candidate_start >= 0 and candidate_end > candidate_start:
            candidate = raw[candidate_start:candidate_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return None


def extract_company_impacts(payload: Any) -> list[dict[str, Any]] | None:
    """Extract the normalized impact list from a model response payload."""
    if isinstance(payload, str):
        payload = _parse_company_payload(payload)
    return extract_impacts_from_payload(payload)


def build_empty_company_result(company: dict[str, Any], peer_groups: dict[str, Any]) -> dict[str, Any]:
    """Return the empty company-opportunist response shape."""
    return {
        "company": company,
        "peer_groups": peer_groups,
        "impacts": [],
    }


def build_company_valid_reference_sets(
    company: dict[str, Any],
    articles: list[dict[str, Any]],
) -> tuple[int, str, set[int]]:
    """Build validation sets for model-produced company impacts."""
    valid_company_id = int(company["company_id"])
    valid_symbol = str(company["symbol"])
    valid_article_ids = {article["article_id"] for article in articles}
    return valid_company_id, valid_symbol, valid_article_ids


def _normalize_relative_positioning(value: Any) -> str:
    relative_positioning = str(value or "").strip().lower()
    replacements = {
        "better": "better_than_peers",
        "advantaged": "better_than_peers",
        "outperform": "better_than_peers",
        "outperforms": "better_than_peers",
        "worse": "worse_than_peers",
        "disadvantaged": "worse_than_peers",
        "underperform": "worse_than_peers",
        "underperforms": "worse_than_peers",
        "same": "similar",
        "inline": "similar",
        "in_line": "similar",
        "neutral": "similar",
        "n/a": "not_applicable",
        "na": "not_applicable",
        "none": "not_applicable",
    }
    relative_positioning = replacements.get(relative_positioning, relative_positioning)
    return relative_positioning if relative_positioning in VALID_RELATIVE_POSITIONING else ""


def normalize_company_impact(
    impact: dict[str, Any],
    *,
    source_article_id: int,
    valid_company_id: int,
    valid_symbol: str,
) -> dict[str, Any] | None:
    """Validate one model-produced company impact before persistence."""
    if int(source_article_id) <= 0:
        return None
    shared_fields = normalize_shared_opportunist_impact_fields(impact)
    if shared_fields is None:
        return None
    relative_positioning = _normalize_relative_positioning(impact.get("relative_positioning"))
    if not relative_positioning:
        return None

    return {
        "article_id": int(source_article_id),
        "company_id": int(valid_company_id),
        "symbol": str(valid_symbol).strip().upper(),
        "relative_positioning": relative_positioning,
        **shared_fields,
    }


def save_company_opportunist_batch_results(
    article_batch: list[dict[str, Any]],
    impacts: list[dict[str, Any]],
    *,
    company_id: int,
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
        for impact in impacts:
            add_company_opportunist_impact(
                article_id=int(impact["article_id"]),
                company_id=int(impact["company_id"]),
                confidence=str(impact["confidence"]),
                impact_direction=str(impact["impact_direction"]),
                impact_magnitude=str(impact["impact_magnitude"]),
                reason=str(impact["reason"]),
                raw_json=impact,
                conn=conn,
            )

        for article in article_batch:
            article_id = int(article["article_id"])
            mark_company_opportunist_article_processed(
                article_id=article_id,
                company_id=int(company_id),
                model=model,
                raw_json={
                    "article_id": article_id,
                    "company_id": int(company_id),
                    "impacts": batch_impacts_by_article.get(article_id, []),
                    "raw_response": raw_response,
                },
                conn=conn,
            )
