from __future__ import annotations

from typing import Any

from agent_analysis.industry_interest import getIndustryScores, getTopThreeIndustries
from agent_analysis.sector_interest import getSectorScores, getTopThreeSectors
from services.config import AgentPipelineSettings


DEFAULT_TOP_SECTOR_COUNT = 3
DEFAULT_TOP_INDUSTRY_COUNT = 3
DEFAULT_TOP_COMPANY_COUNT = 3
RANKING_MAX_AGE_DAYS_UNSET = object()


def _pipeline_settings() -> AgentPipelineSettings:
    return AgentPipelineSettings.from_env()


def resolve_ranking_max_age_days(value: int | None | object) -> int | None:
    if value is RANKING_MAX_AGE_DAYS_UNSET:
        return _pipeline_settings().ranking_max_age_days
    return value if value is None or isinstance(value, int) else _pipeline_settings().ranking_max_age_days


def _get_top_sector_keys(
    *,
    top_sector_count: int,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> list[str]:
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    return [
        sector_key
        for sector_key, _score in getTopThreeSectors(
            getSectorScores(max_age_days=ranking_max_age_days)
        )[: max(0, int(top_sector_count))]
    ]


def _get_top_industry_keys(
    sector_key: str,
    *,
    top_industry_count: int,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> list[str]:
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    return [
        industry_key
        for industry_key, _score in getTopThreeIndustries(
            getIndustryScores(
                sector_key,
                max_age_days=ranking_max_age_days,
            )
        )[: max(0, int(top_industry_count))]
    ]


def _get_ranked_sectors(
    *,
    top_sector_count: int,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> list[dict[str, Any]]:
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    ranked = getTopThreeSectors(
        getSectorScores(max_age_days=ranking_max_age_days)
    )[: max(0, int(top_sector_count))]
    return [
        {
            "sector_key": sector_key,
            "score": score,
        }
        for sector_key, score in ranked
    ]


def _get_ranked_industries_for_sector(
    sector_key: str,
    *,
    top_industry_count: int,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> list[dict[str, Any]]:
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    ranked = getTopThreeIndustries(
        getIndustryScores(
            sector_key,
            max_age_days=ranking_max_age_days,
        )
    )[: max(0, int(top_industry_count))]
    return [
        {
            "industry_key": industry_key,
            "score": score,
        }
        for industry_key, score in ranked
    ]


def get_current_rankings(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> dict[str, Any]:
    """Return the current ranked sectors and industries without running the pipeline."""
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    ranked_sectors = _get_ranked_sectors(
        top_sector_count=top_sector_count,
        ranking_max_age_days=ranking_max_age_days,
    )
    ranked_industries_by_sector = {
        sector["sector_key"]: _get_ranked_industries_for_sector(
            sector["sector_key"],
            top_industry_count=top_industry_count,
            ranking_max_age_days=ranking_max_age_days,
        )
        for sector in ranked_sectors
    }

    return {
        "top_sectors": ranked_sectors,
        "top_industries_by_sector": ranked_industries_by_sector,
        "ranking_max_age_days": ranking_max_age_days,
    }
