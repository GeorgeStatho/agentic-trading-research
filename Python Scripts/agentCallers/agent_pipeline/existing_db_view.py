from __future__ import annotations

from typing import Any, Callable

from agent_helpers.company_opportunist import get_company_opportunist_summary
from agent_contracts import (
    PipelineIndustryResult,
    PipelineRunResult,
    PipelineSectorResult,
)
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


ProgressCallback = Callable[[dict[str, Any]], None]


def _emit_progress(
    on_progress: ProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    if on_progress is None:
        return
    on_progress(payload)


def _build_industry_result_from_existing_data(
    industry_key: str,
    *,
    top_company_count: int,
    ranking_max_age_days: int | None,
    on_progress: ProgressCallback | None = None,
    sector_key: str | None = None,
) -> PipelineIndustryResult:
    # Assemble the industry result from existing data so callers can work from one normalized shape.
    _emit_progress(
        on_progress,
        {
            "stage": "pipeline_industry",
            "message": f"Loading industry context for {industry_key}",
            "current_sector": sector_key,
            "current_industry": industry_key,
        },
    )
    company_selection = collect_ranked_companies_for_industry(
        industry_key,
        top_company_count=top_company_count,
    )
    for company in company_selection["selected_companies"]:
        _emit_progress(
            on_progress,
            {
                "stage": "pipeline_company",
                "message": f"Loading company context for {company['symbol']}",
                "current_symbol": company["symbol"],
                "current_company_name": company.get("name"),
                "current_sector": sector_key,
                "current_industry": (
                    company_selection["industry"].get("name")
                    or company_selection["industry"].get("industry_key")
                    or industry_key
                ),
            },
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
    on_progress: ProgressCallback | None = None,
) -> PipelineSectorResult:
    # Assemble the sector result from existing data so callers can work from one normalized shape.
    _emit_progress(
        on_progress,
        {
            "stage": "pipeline_sector",
            "message": f"Loading sector context for {sector_key}",
            "current_sector": sector_key,
        },
    )
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
            on_progress=on_progress,
            sector_key=sector_key,
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
    on_progress: ProgressCallback | None = None,
) -> PipelineRunResult:
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
            on_progress=on_progress,
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
