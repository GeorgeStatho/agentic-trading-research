from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from CompanyOppurtunist import classify_company_articles
from CompanyOppurtunityBuilder import get_industry_company_groups
from IndustryOppuruntinst import classify_sector_articles_to_industries
from SectorOppurtunist import classify_sector_articles
from industryIntrest import getIndustryScores, getTopThreeIndustries
from sectorIntrest import getSectorScores, getTopThreeSectors

from _company_opportunist_helpers import get_company_opportunist_summary

DEFAULT_TOP_SECTOR_COUNT = 3
DEFAULT_TOP_INDUSTRY_COUNT = 3
DEFAULT_TOP_COMPANY_COUNT = 3

__all__ = [
    "collect_ranked_companies_for_industry",
    "build_company_opportunist_summary",
    "run_agent_pipeline",
]


def _slice_companies(companies: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return companies[: max(0, int(limit))]


def collect_ranked_companies_for_industry(
    industry_identifier: str,
    *,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
) -> dict[str, Any]:
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


def _get_top_sector_keys(*, top_sector_count: int) -> list[str]:
    return [
        sector_key
        for sector_key, _score in getTopThreeSectors(getSectorScores())[: max(0, int(top_sector_count))]
    ]


def _get_top_industry_keys(
    sector_key: str,
    *,
    top_industry_count: int,
) -> list[str]:
    return [
        industry_key
        for industry_key, _score in getTopThreeIndustries(getIndustryScores(sector_key))[: max(0, int(top_industry_count))]
    ]


def run_agent_pipeline(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
) -> dict[str, Any]:
    sector_results: list[dict[str, Any]] = []

    top_sector_keys = _get_top_sector_keys(top_sector_count=top_sector_count)
    for sector_key in top_sector_keys:
        print("Processing Sectors: "+sector_key)
        sector_opportunist_result = classify_sector_articles(sector_key)
        industry_opportunist_result = classify_sector_articles_to_industries(sector_key)

        industry_results: list[dict[str, Any]] = []
        top_industry_keys = _get_top_industry_keys(
            sector_key,
            top_industry_count=top_industry_count,
        )
        for industry_key in top_industry_keys:
            print("Processing industry: "+industry_key)
            company_selection = collect_ranked_companies_for_industry(
                industry_key,
                top_company_count=top_company_count,
            )

            company_results: list[dict[str, Any]] = []
            for company in company_selection["selected_companies"]:
                print("Processing: " +company["symbol"])
                company_result = classify_company_articles(company["symbol"])
                company_results.append(company_result)
                print("Finished: " +company["symbol"])

            company_opportunist_summaries: list[dict[str, Any]] = []
            for result in company_results:
                summary = build_company_opportunist_summary(result)
                company_opportunist_summaries.append(summary)

            industry_results.append(
                {
                    "industry": company_selection["industry"],
                    "selected_companies": company_selection["selected_companies"],
                    "company_opportunist_summaries": company_opportunist_summaries,
                }
            )
            print("Finished industry:"+industry_key)

        sector_results.append(
            {
                "sector_key": sector_key,
                "sector_opportunist_result": sector_opportunist_result,
                "industry_opportunist_result": industry_opportunist_result,
                "industries": industry_results,
            }
        )
        print("Finished Sector:"+sector_key)

    return {
        "top_sector_count": top_sector_count,
        "top_industry_count": top_industry_count,
        "top_company_count": top_company_count,
        "sectors": sector_results,
    }


if __name__ == "__main__":
    #result=run_agent_pipeline()
    #print(json.dumps(result,ensure_ascii=True, indent=2))
    #with open("agent_pipeline_output","w",encoding="utf-8")  as handle:
    #    json.dump(result, handle, ensure_ascii=True, indent=2)
    print(get_company_opportunist_summary("NVDA"))
