from __future__ import annotations

from typing import Any

from agent_builders.company_opportunity import get_industry_company_groups

from agent_pipeline.ranking import (
    DEFAULT_TOP_COMPANY_COUNT,
    DEFAULT_TOP_INDUSTRY_COUNT,
    DEFAULT_TOP_SECTOR_COUNT,
    RANKING_MAX_AGE_DAYS_UNSET,
    get_current_rankings,
    resolve_ranking_max_age_days,
)


def _slice_companies(companies: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return companies[: max(0, int(limit))]


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def collect_ranked_companies_for_industry(
    industry_identifier: str,
    *,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
) -> dict[str, Any]:
    """Return the top ranked companies for one industry for downstream stages."""
    company_groups = get_industry_company_groups(industry_identifier)
    top_companies = _slice_companies(
        company_groups.get("top_companies", []),
        top_company_count,
    )

    selected_companies = [
        {
            "company_id": int(company["company_id"]),
            "symbol": company["symbol"],
            "name": company["name"],
            "rating": company.get("rating"),
            "market_weight": company.get("market_weight"),
            "source_groups": ["top_companies"],
        }
        for company in top_companies
    ]

    return {
        "industry": company_groups["industry"],
        "selected_companies": selected_companies,
    }


def build_company_opportunist_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize one company opportunist result into compact dashboard counts."""
    company = result.get("company", {})
    impacts = result.get("impacts", [])

    confidence_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    magnitude_counts: dict[str, int] = {}
    reasons: list[str] = []

    for impact in impacts:
        confidence = str(impact.get("confidence") or "").strip().lower()
        impact_direction = str(impact.get("impact_direction") or "").strip().lower()
        impact_magnitude = str(impact.get("impact_magnitude") or "").strip().lower()
        reason = str(impact.get("reason") or "").strip()

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
        "impact_count": len(impacts),
        "confidence_counts": confidence_counts,
        "direction_counts": direction_counts,
        "magnitude_counts": magnitude_counts,
        "sample_reasons": reasons[:3],
    }


def get_current_pipeline_targets(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> dict[str, Any]:
    """Return the sectors, industries, and companies that the pipeline would target now."""
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    rankings = get_current_rankings(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
        ranking_max_age_days=ranking_max_age_days,
    )
    top_sector_keys = [sector["sector_key"] for sector in rankings["top_sectors"]]

    top_industry_keys: list[str] = []
    selected_companies: list[dict[str, Any]] = []
    selected_company_ids: list[int] = []
    selected_company_symbols: list[str] = []

    for sector_key in top_sector_keys:
        industry_keys = [
            industry["industry_key"]
            for industry in rankings["top_industries_by_sector"].get(sector_key, [])
        ]
        top_industry_keys.extend(industry_keys)

        for industry_key in industry_keys:
            company_selection = collect_ranked_companies_for_industry(
                industry_key,
                top_company_count=top_company_count,
            )
            for company in company_selection["selected_companies"]:
                company_id = int(company["company_id"])
                if company_id in selected_company_ids:
                    continue
                selected_companies.append(company)
                selected_company_ids.append(company_id)
                selected_company_symbols.append(str(company["symbol"]))

    return {
        "rankings": rankings,
        "ranking_max_age_days": ranking_max_age_days,
        "top_sector_keys": _dedupe_preserving_order(top_sector_keys),
        "top_industry_keys": _dedupe_preserving_order(top_industry_keys),
        "selected_companies": selected_companies,
        "selected_company_ids": selected_company_ids,
        "selected_company_symbols": _dedupe_preserving_order(selected_company_symbols),
    }
