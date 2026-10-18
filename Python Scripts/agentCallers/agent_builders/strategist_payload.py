from __future__ import annotations

from datetime import datetime, timezone
import json

from agent_contracts import PayloadFilters, StrategistInputPayload, StrategistViewsPayload
from agent_helpers.strategist import DEFAULT_MAX_ARTICLE_AGE_DAYS, build_strategist_evidence_sections
from news_db_modules.bootstrap import initialize_database as initialize_news_database

from agent_builders.strategist_payload_articles import (
    _build_supporting_articles,
    _build_view,
    _serialize_signal,
)
from agent_stages.strategist_grounding import build_strategist_grounding
from agent_builders import strategist_payload_company as _company_helpers
from agent_builders.strategist_payload_company import (
    _build_company_price_context,
    _deserialize_company_raw_json,
    _get_company_market_record,
    _serialize_company_scope,
)
from agent_builders.strategist_payload_price import (
    _build_company_historical_price_data,
    _empty_historical_snapshot,
    _empty_trend_quality_payload,
    _extract_close_values_from_frame,
    _fetch_ticker_history_frame,
    _safe_float,
    _safe_int,
    _serialize_timestamp,
    _summarize_history_frame,
    _summarize_trend_quality_frame,
    _summarize_trend_quality_from_closes,
    DEFAULT_TREND_QUALITY_RISK_FREE_RATE,
    DEFAULT_TREND_QUALITY_TRADING_PERIODS_PER_YEAR,
    HISTORICAL_PERIOD_CONFIG,
    TREND_QUALITY_WINDOW_CONFIG,
)
from agent_builders.strategist_payload_rollup import (
    _build_grouped_rollups,
    _build_opportunist_rollup,
    _build_rollup_section,
)
from agent_builders.strategist_payload_rollup_common import (
    _build_empty_count_map,
    _build_empty_weight_map,
    _build_top_reasons,
    _get_signal_weight,
    _normalize_reason_key,
    _pick_dominant_bucket,
    _round_rollup_metric,
    CONFIDENCE_WEIGHTS,
    EFFECT_TYPE_WEIGHTS,
    MAGNITUDE_WEIGHTS,
    MATERIALITY_WEIGHTS,
    ROLLUP_DIRECTIONS,
    ROLLUP_EFFECT_TYPES,
    ROLLUP_HORIZONS,
    ROLLUP_MATERIALITY,
    ROLLUP_RELATIVE_POSITIONING,
)


DEFAULT_SUMMARY_ARTICLE_LIMIT = 20
DEFAULT_FULL_ARTICLE_LIMIT = 5

__all__ = [
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "DEFAULT_SUMMARY_ARTICLE_LIMIT",
    "DEFAULT_FULL_ARTICLE_LIMIT",
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
