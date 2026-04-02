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


DEFAULT_TOP_SECTOR_COUNT = 3
DEFAULT_TOP_INDUSTRY_COUNT = 3
DEFAULT_COMPANIES_PER_GROUP = 3

__all__ = [
    "collect_ranked_companies_for_industry",
    "run_agent_pipeline",
]


def _slice_companies(companies: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return companies[: max(0, int(limit))]


def collect_ranked_companies_for_industry(
    industry_identifier: str,
    *,
    companies_per_group: int = DEFAULT_COMPANIES_PER_GROUP,
) -> dict[str, Any]:
    company_groups = get_industry_company_groups(industry_identifier)

    top_companies = _slice_companies(company_groups.get("top_companies", []), companies_per_group)
    top_growth_companies = _slice_companies(company_groups.get("top_growth_companies", []), companies_per_group)
    top_performing_companies = _slice_companies(company_groups.get("top_performing_companies", []), companies_per_group)

    deduped_companies: dict[int, dict[str, Any]] = {}
    for group_name, companies in (
        ("top_companies", top_companies),
        ("top_growth_companies", top_growth_companies),
        ("top_performing_companies", top_performing_companies),
    ):
        for company in companies:
            company_id = int(company["company_id"])
            entry = deduped_companies.setdefault(
                company_id,
                {
                    "company_id": company_id,
                    "symbol": company["symbol"],
                    "name": company["name"],
                    "rating": company.get("rating"),
                    "market_weight": company.get("market_weight"),
                    "source_groups": [],
                },
            )
            if group_name not in entry["source_groups"]:
                entry["source_groups"].append(group_name)

    return {
        "industry": company_groups["industry"],
        "top_companies": top_companies,
        "top_growth_companies": top_growth_companies,
        "top_performing_companies": top_performing_companies,
        "selected_companies": list(deduped_companies.values()),
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
    companies_per_group: int = DEFAULT_COMPANIES_PER_GROUP,
) -> dict[str, Any]:
    sector_results: list[dict[str, Any]] = []

    for sector_key in _get_top_sector_keys(top_sector_count=top_sector_count):
        sector_opportunist_result = classify_sector_articles(sector_key)
        industry_opportunist_result = classify_sector_articles_to_industries(sector_key)

        industry_results: list[dict[str, Any]] = []
        for industry_key in _get_top_industry_keys(
            sector_key,
            top_industry_count=top_industry_count,
        ):
            company_selection = collect_ranked_companies_for_industry(
                industry_key,
                companies_per_group=companies_per_group,
            )

            company_results = [
                classify_company_articles(company["symbol"])
                for company in company_selection["selected_companies"]
            ]

            industry_results.append(
                {
                    "industry": company_selection["industry"],
                    "company_selection": company_selection,
                    "company_opportunist_results": company_results,
                }
            )

        sector_results.append(
            {
                "sector_key": sector_key,
                "sector_opportunist_result": sector_opportunist_result,
                "industry_opportunist_result": industry_opportunist_result,
                "industries": industry_results,
            }
        )

    return {
        "top_sector_count": top_sector_count,
        "top_industry_count": top_industry_count,
        "companies_per_group": companies_per_group,
        "sectors": sector_results,
    }


if __name__ == "__main__":
    print(json.dumps(run_agent_pipeline(), ensure_ascii=True, indent=2))
