from __future__ import annotations

from typing import Any

from agent_helpers.company_opportunist import get_company_opportunist_summary
from agent_pipeline.ranking import (
    DEFAULT_TOP_COMPANY_COUNT,
    DEFAULT_TOP_INDUSTRY_COUNT,
    DEFAULT_TOP_SECTOR_COUNT,
    RANKING_MAX_AGE_DAYS_UNSET,
    _get_ranked_industries_for_sector,
    get_current_rankings,
    resolve_ranking_max_age_days,
)
from agent_pipeline.target_selection import collect_ranked_companies_for_industry


def _build_industry_result_from_existing_data(
    industry_key: str,
    *,
    top_company_count: int,
    ranking_max_age_days: int | None,
) -> dict[str, Any]:
    company_selection = collect_ranked_companies_for_industry(
        industry_key,
        top_company_count=top_company_count,
    )
    company_opportunist_summaries = [
        get_company_opportunist_summary(
            company["symbol"],
            max_age_days=ranking_max_age_days,
        )
        for company in company_selection["selected_companies"]
    ]
    return {
        "industry": company_selection["industry"],
        "selected_companies": company_selection["selected_companies"],
        "company_opportunist_summaries": company_opportunist_summaries,
    }


def _build_sector_result_from_existing_data(
    sector_key: str,
    *,
    top_industry_count: int,
    top_company_count: int,
    ranking_max_age_days: int | None,
) -> dict[str, Any]:
    top_industry_rankings = _get_ranked_industries_for_sector(
        sector_key,
        top_industry_count=top_industry_count,
        ranking_max_age_days=ranking_max_age_days,
    )
    industry_results = [
        _build_industry_result_from_existing_data(
            industry["industry_key"],
            top_company_count=top_company_count,
            ranking_max_age_days=ranking_max_age_days,
        )
        for industry in top_industry_rankings
    ]
    return {
        "sector_key": sector_key,
        "top_industries": top_industry_rankings,
        "sector_opportunist_result": None,
        "industry_opportunist_result": None,
        "industries": industry_results,
    }


def run_agent_pipeline_from_existing_data(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> dict[str, Any]:
    """Build the current pipeline view from persisted DB state without scraping."""
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    rankings_before_sector_stage = get_current_rankings(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
        ranking_max_age_days=ranking_max_age_days,
    )
    top_sector_keys = [sector["sector_key"] for sector in rankings_before_sector_stage["top_sectors"]]
    sector_results = [
        _build_sector_result_from_existing_data(
            sector_key,
            top_industry_count=top_industry_count,
            top_company_count=top_company_count,
            ranking_max_age_days=ranking_max_age_days,
        )
        for sector_key in top_sector_keys
    ]
    rankings_after_pipeline = get_current_rankings(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
        ranking_max_age_days=ranking_max_age_days,
    )

    return {
        "top_sector_count": top_sector_count,
        "top_industry_count": top_industry_count,
        "top_company_count": top_company_count,
        "ranking_max_age_days": ranking_max_age_days,
        "pipeline_mode": "existing_db",
        "macro_news_to_sectors": {
            "us": [],
            "world": [],
        },
        "scrape_results": {
            "macro_news": {
                "us": 0,
                "world": 0,
            },
            "sectors": {},
            "industries": {},
            "companies": {},
        },
        "rankings": {
            "before_sector_stage": rankings_before_sector_stage,
            "after_pipeline": rankings_after_pipeline,
        },
        "sectors": sector_results,
    }
