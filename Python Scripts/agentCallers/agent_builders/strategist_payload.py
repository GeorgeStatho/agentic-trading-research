from __future__ import annotations

from datetime import datetime, timezone
import json

from agent_contracts import PayloadFilters, StrategistInputPayload, StrategistViewsPayload
from agent_helpers.strategist_evidence import DEFAULT_MAX_ARTICLE_AGE_DAYS, build_strategist_evidence_sections
from news_db_modules.bootstrap import initialize_database as initialize_news_database

from agent_builders.strategist_payload_articles import (
    SUMMARY_ARTICLE_QUOTAS,
    SUPPORTING_ARTICLE_SELECTION_VERSION,
    _build_supporting_articles,
    _build_view,
)
from agent_stages.strategist_grounding import build_strategist_grounding
from agent_builders.strategist_payload_company import (
    _serialize_company_scope,
)
from agent_builders.strategist_payload_rollup import (
    _build_opportunist_rollup,
)


DEFAULT_SUMMARY_ARTICLE_LIMIT = 20
DEFAULT_FULL_ARTICLE_LIMIT = 5

__all__ = [
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "DEFAULT_SUMMARY_ARTICLE_LIMIT",
    "DEFAULT_FULL_ARTICLE_LIMIT",
    "SUMMARY_ARTICLE_QUOTAS",
    "SUPPORTING_ARTICLE_SELECTION_VERSION",
    "build_strategist_input",
    "build_strategist_prompt_payload",
]


def _serialize_filters(
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
    summary_article_limit: int,
    full_article_limit: int,
) -> PayloadFilters:
    return {
        "start_time": start_time.astimezone(timezone.utc).isoformat() if start_time is not None else "",
        "end_time": end_time.astimezone(timezone.utc).isoformat() if end_time is not None else "",
        "max_age_days": max_age_days,
        "macro_confidence": "high+medium",
        "sector_confidence": "high+medium",
        "industry_confidence": "high+medium",
        "company_confidence": "high+medium",
        "sector_news_source": "cnbc_rss",
        "summary_article_limit": summary_article_limit,
        "full_article_limit": full_article_limit,
    }


def build_strategist_input(
    company_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    summary_article_limit: int = DEFAULT_SUMMARY_ARTICLE_LIMIT,
    full_article_limit: int = DEFAULT_FULL_ARTICLE_LIMIT,
) -> StrategistInputPayload:
    initialize_news_database()
    evidence = build_strategist_evidence_sections(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    company = evidence["company"]
    opportunist_rollup = _build_opportunist_rollup(evidence)

    views: StrategistViewsPayload = {
        "macro_view": _build_view(
            layer="macro_view",
            items=evidence["macro_impacts"],
            label="High-confidence macro news mapped to the company sector",
        ),
        "sector_view": _build_view(
            layer="sector_view",
            items=evidence["sector_impacts"],
            label="High-confidence sector opportunist impacts",
        ),
        "industry_view": _build_view(
            layer="industry_view",
            items=evidence["industry_impacts"],
            label="High-confidence industry opportunist impacts",
        ),
        "company_view": _build_view(
            layer="company_view",
            items=evidence["company_impacts"],
            label="High-confidence company opportunist impacts",
        ),
    }

    serialized_company = _serialize_company_scope(company)
    payload = {
        "company": serialized_company,
        "peer_groups": evidence["peer_groups"],
        "filters": _serialize_filters(
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
            summary_article_limit=summary_article_limit,
            full_article_limit=full_article_limit,
        ),
        "opportunist_rollup": opportunist_rollup,
        "views": views,
        "supporting_articles": _build_supporting_articles(
            evidence,
            summary_article_limit=summary_article_limit,
            full_article_limit=full_article_limit,
        ),
    }
    from agent_builders.company_evidence_payload import build_company_evidence_payload
    payload["company_evidence"] = build_company_evidence_payload(
        serialized_company["company_id"],
        as_of=end_time.astimezone(timezone.utc).isoformat() if end_time else None,
    )
    payload["grounding"] = build_strategist_grounding(payload)
    # The decision time, so the model can judge article and filing age; a replay's end_time keeps it deterministic.
    payload["as_of"] = (end_time or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    return payload


def build_strategist_prompt_payload(
    company_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    summary_article_limit: int = DEFAULT_SUMMARY_ARTICLE_LIMIT,
    full_article_limit: int = DEFAULT_FULL_ARTICLE_LIMIT,
) -> str:
    payload = build_strategist_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
    )
    return json.dumps(payload, ensure_ascii=True, indent=2)


if __name__ == "__main__":
    print(build_strategist_prompt_payload("AAPL"))
