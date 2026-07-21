from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


VALID_CLAIM_TYPES = {
    "summary",
    "why_now",
    "catalyst",
    "thesis",
    "risk",
    "watchlist_reason",
}


def _positive_article_ids(items: Any) -> set[int]:
    article_ids: set[int] = set()
    if not isinstance(items, list):
        return article_ids
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            article_id = int(item.get("article_id"))
        except (TypeError, ValueError):
            continue
        if article_id > 0:
            article_ids.add(article_id)
    return article_ids


def build_strategist_grounding(payload: dict[str, Any]) -> dict[str, Any]:
    company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
    supporting_articles = payload.get("supporting_articles") if isinstance(payload.get("supporting_articles"), dict) else {}
    article_ids = _positive_article_ids(supporting_articles.get("article_summaries"))
    article_ids.update(_positive_article_ids(supporting_articles.get("full_articles")))

    views = payload.get("views") if isinstance(payload.get("views"), dict) else {}
    for view in views.values():
        if isinstance(view, dict):
            article_ids.update(_positive_article_ids(view.get("agent_conclusions")))

    return {
        "company": {
            "id": company.get("company_id"),
            "symbol": str(company.get("symbol") or "").strip().upper(),
            "name": str(company.get("name") or "").strip(),
        },
        "industry": {
            "id": company.get("industry_id"),
            "key": str(company.get("industry_key") or "").strip(),
            "name": str(company.get("industry_name") or "").strip(),
        },
        "sector": {
            "id": company.get("sector_id"),
            "key": str(company.get("sector_key") or "").strip(),
            "name": str(company.get("sector_name") or "").strip(),
        },
        "article_ids": sorted(article_ids),
        "market_context_as_of": datetime.now(timezone.utc).isoformat(),
    }


def _normalized_claims(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Strategist response must include an evidence_claims array.")

    normalized_claims: list[dict[str, Any]] = []
    for claim in value:
        if not isinstance(claim, dict):
            raise ValueError("Strategist evidence claim must be an object.")
        claim_type = str(claim.get("claim_type") or "").strip().lower()
        claim_text = str(claim.get("claim") or "").strip()
        evidence_ids = claim.get("evidence_article_ids")
        if claim_type not in VALID_CLAIM_TYPES or not claim_text:
            raise ValueError("Strategist evidence claim has an invalid type or empty claim.")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError("Strategist evidence claim must cite at least one article ID.")
        if any(isinstance(article_id, bool) or not isinstance(article_id, int) for article_id in evidence_ids):
            raise ValueError("Strategist evidence claim article IDs must be integers.")
        normalized_claims.append(
            {
                "claim_type": claim_type,
                "claim": claim_text,
                "evidence_article_ids": evidence_ids,
            }
        )
    return normalized_claims


def validate_grounded_recommendation(
    recommendation: dict[str, Any],
    *,
    grounding: dict[str, Any],
) -> dict[str, Any]:
    expected_symbol = str(grounding.get("company", {}).get("symbol") or "").strip().upper()
    response_symbol = str(recommendation.get("grounded_company_symbol") or "").strip().upper()
    if not expected_symbol or response_symbol != expected_symbol:
        raise ValueError("Strategist response referenced a company outside the grounded payload.")

    entity_ids = recommendation.get("grounded_entity_ids")
    if not isinstance(entity_ids, dict):
        raise ValueError("Strategist response must include grounded company, industry, and sector IDs.")
    for entity_name, grounding_key in (
        ("company", "company_id"),
        ("industry", "industry_id"),
        ("sector", "sector_id"),
    ):
        expected_id = grounding.get(entity_name, {}).get("id")
        if expected_id is None:
            continue
        try:
            response_id = int(entity_ids.get(grounding_key))
        except (TypeError, ValueError):
            raise ValueError(f"Strategist response has an invalid grounded {entity_name} ID.") from None
        if response_id != int(expected_id):
            raise ValueError(f"Strategist response referenced a {entity_name} outside the grounded payload.")

    allowed_article_ids = {
        int(article_id)
        for article_id in grounding.get("article_ids", [])
        if isinstance(article_id, int) and article_id > 0
    }
    claims = _normalized_claims(recommendation.get("evidence_claims"))
    if not allowed_article_ids:
        if claims:
            raise ValueError("Strategist response cited articles when no articles were supplied.")
        return {
            **recommendation,
            "evidence_claims": claims,
            "grounded_company_symbol": response_symbol,
            "grounded_entity_ids": entity_ids,
        }

    claim_counts: dict[str, int] = {claim_type: 0 for claim_type in VALID_CLAIM_TYPES}
    for claim in claims:
        claim_counts[claim["claim_type"]] += 1
        if any(article_id not in allowed_article_ids for article_id in claim["evidence_article_ids"]):
            raise ValueError("Strategist response cited an article outside the grounded payload.")

    required_claim_counts = {
        "summary": 1,
        "why_now": 1,
        "catalyst": len(recommendation.get("catalyst") or []),
        "thesis": len(recommendation.get("thesis") or []),
        "risk": len(recommendation.get("risks") or []),
        "watchlist_reason": 1 if str(recommendation.get("watchlist_reason") or "").strip() else 0,
    }
    for claim_type, minimum_count in required_claim_counts.items():
        if claim_counts[claim_type] < minimum_count:
            raise ValueError(f"Strategist response is missing grounded {claim_type} claim citations.")

    return {
        **recommendation,
        "evidence_claims": claims,
        "grounded_company_symbol": response_symbol,
        "grounded_entity_ids": entity_ids,
    }
