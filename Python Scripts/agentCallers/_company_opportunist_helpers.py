from __future__ import annotations

from datetime import datetime
import json
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

from CompanyOppurtunityBuilder import get_company_linked_articles, get_industry_company_groups
from db_helpers import (
    DB_PATH,
    add_company_opportunist_impact,
    get_connection,
    initialize_news_database,
    mark_company_opportunist_article_processed,
)


VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}
VALID_IMPACT_DIRECTIONS = {"positive", "negative"}
VALID_IMPACT_MAGNITUDES = {"major", "moderate", "modest"}

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
    return sorted(
        articles,
        key=lambda article: (
            str(article.get("published_at") or ""),
            int(article.get("article_id") or 0),
        ),
        reverse=True,
    )


def _load_processed_article_ids(article_ids: list[int], company_id: int) -> set[int]:
    if not article_ids:
        return set()

    placeholders = ",".join("?" for _ in article_ids)
    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT article_id
            FROM company_opportunist_article_processing
            WHERE company_id = ?
              AND article_id IN ({placeholders})
            """,
            (int(company_id), *tuple(article_ids)),
        ).fetchall()

    return {int(row["article_id"]) for row in rows}


def _filter_unprocessed_articles(articles: list[dict[str, Any]], company_id: int) -> list[dict[str, Any]]:
    processed_article_ids = _load_processed_article_ids(
        [int(article["article_id"]) for article in articles],
        company_id,
    )
    if not processed_article_ids:
        return articles

    return [
        article
        for article in articles
        if int(article["article_id"]) not in processed_article_ids
    ]


def build_company_opportunist_articles(
    company_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
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
    if isinstance(payload, str):
        payload = _parse_company_payload(payload)

    if isinstance(payload, list):
        return payload
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


def build_empty_company_result(company: dict[str, Any], peer_groups: dict[str, Any]) -> dict[str, Any]:
    return {
        "company": company,
        "peer_groups": peer_groups,
        "impacts": [],
    }


def build_company_valid_reference_sets(
    company: dict[str, Any],
    articles: list[dict[str, Any]],
) -> tuple[int, str, set[int]]:
    valid_company_id = int(company["company_id"])
    valid_symbol = str(company["symbol"])
    valid_article_ids = {article["article_id"] for article in articles}
    return valid_company_id, valid_symbol, valid_article_ids


def normalize_company_impact(
    impact: dict[str, Any],
    *,
    source_article_id: int,
    valid_company_id: int,
    valid_symbol: str,
) -> dict[str, Any] | None:
    confidence = str(impact.get("confidence") or "").strip().lower()
    impact_direction = str(impact.get("impact_direction") or "").strip().lower()
    impact_magnitude = str(impact.get("impact_magnitude") or "").strip().lower()
    reason = str(impact.get("reason") or "").strip()

    if int(source_article_id) <= 0:
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
        "article_id": int(source_article_id),
        "company_id": int(valid_company_id),
        "symbol": str(valid_symbol).strip().upper(),
        "confidence": confidence,
        "impact_direction": impact_direction,
        "impact_magnitude": impact_magnitude,
        "reason": reason,
    }


def save_company_opportunist_batch_results(
    article_batch: list[dict[str, Any]],
    impacts: list[dict[str, Any]],
    *,
    company_id: int,
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
