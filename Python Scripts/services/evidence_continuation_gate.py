from __future__ import annotations

"""Deterministic daily swing-continuation eligibility for option entries.

This module intentionally does not select contracts, place orders, or alter the
manager recommendation.  It only reports whether a proposed directional idea
has enough stored evidence and daily technical confirmation to continue to the
deterministic option selector.
"""

from dataclasses import dataclass
import os
from typing import Any


POLICY_VERSION = "evidence-continuation-v1"


@dataclass(frozen=True)
class EvidenceContinuationPolicy:
    enabled: bool = True
    required_evidence_count: int = 2
    required_independent_sources: int = 2
    min_evidence_score: float = 0.65
    require_sector_outperformance: bool = True
    require_stock_relative_strength: bool = True
    min_relative_volume: float = 1.25
    max_overextension: str = "medium"
    allowed_call_entry_quality: frozenset[str] = frozenset({"good", "excellent"})
    allowed_put_entry_quality: frozenset[str] = frozenset({"good", "excellent"})
    fail_closed_on_missing_data: bool = True
    enforcement_mode: str = "shadow"
    policy_version: str = POLICY_VERSION

    @classmethod
    def from_env(cls) -> "EvidenceContinuationPolicy":
        def flag(name: str, default: bool) -> bool:
            return str(os.getenv(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}

        def positive_int(name: str, default: int) -> int:
            try:
                return max(1, int(os.getenv(name, str(default))))
            except ValueError:
                return default

        def positive_float(name: str, default: float) -> float:
            try:
                return max(0.0, float(os.getenv(name, str(default))))
            except ValueError:
                return default

        def qualities(name: str, default: frozenset[str]) -> frozenset[str]:
            values = {_text(value) for value in os.getenv(name, ",".join(sorted(default))).split(",")}
            return frozenset(value for value in values if value) or default

        max_overextension = _text(os.getenv("EVIDENCE_CONTINUATION_MAX_OVEREXTENSION", "medium"))
        enforcement_mode = _text(os.getenv("EVIDENCE_CONTINUATION_MODE", "shadow"))
        return cls(
            enabled=flag("EVIDENCE_CONTINUATION_ENABLED", True),
            required_evidence_count=positive_int("EVIDENCE_CONTINUATION_REQUIRED_EVIDENCE_COUNT", 2),
            required_independent_sources=positive_int("EVIDENCE_CONTINUATION_REQUIRED_INDEPENDENT_SOURCES", 2),
            min_evidence_score=positive_float("EVIDENCE_CONTINUATION_MIN_EVIDENCE_SCORE", 0.65),
            require_sector_outperformance=flag("EVIDENCE_CONTINUATION_REQUIRE_SECTOR_OUTPERFORMANCE", True),
            require_stock_relative_strength=flag("EVIDENCE_CONTINUATION_REQUIRE_STOCK_RELATIVE_STRENGTH", True),
            min_relative_volume=positive_float("EVIDENCE_CONTINUATION_MIN_RELATIVE_VOLUME", 1.25),
            max_overextension=max_overextension if max_overextension in {"low", "medium", "high"} else "medium",
            allowed_call_entry_quality=qualities("EVIDENCE_CONTINUATION_ALLOWED_CALL_QUALITIES", frozenset({"good", "excellent"})),
            allowed_put_entry_quality=qualities("EVIDENCE_CONTINUATION_ALLOWED_PUT_QUALITIES", frozenset({"good", "excellent"})),
            fail_closed_on_missing_data=flag("EVIDENCE_CONTINUATION_FAIL_CLOSED_ON_MISSING_DATA", True),
            enforcement_mode=enforcement_mode if enforcement_mode in {"enforce", "shadow"} else "shadow",
        )


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


DEFAULT_EVIDENCE_CONTINUATION_POLICY = EvidenceContinuationPolicy.from_env()


def _supporting_articles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    supporting = payload.get("supporting_articles")
    summaries = supporting.get("article_summaries") if isinstance(supporting, dict) else []
    return [article for article in summaries if isinstance(article, dict)] if isinstance(summaries, list) else []


def _evidence_snapshot(payload: dict[str, Any], policy: EvidenceContinuationPolicy) -> dict[str, Any]:
    grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else {}
    allowed_ids = {
        int(article_id)
        for article_id in grounding.get("article_ids", [])
        if isinstance(article_id, int) and article_id > 0
    }
    eligible_articles: list[dict[str, Any]] = []
    invalid_grounding = False
    seen_article_ids = set()
    for article in _supporting_articles(payload):
        article_id = article.get("article_id")
        score = _float(article.get("evidence_score"))
        if not isinstance(article_id, int) or article_id <= 0 or article_id not in allowed_ids:
            invalid_grounding = True
            continue
        if score is None or score < policy.min_evidence_score:
            continue
        if article_id not in seen_article_ids:
            eligible_articles.append(article)
            seen_article_ids.add(article_id)

    sources = {
        (_text(article.get("root_event_id")) if article.get("primary_ticker")
         else _text(article.get("root_source_id")))
        for article in eligible_articles
        if _text(article.get("root_event_id")) and _text(article.get("root_source_id"))
        and article.get("root_source_id") != "unverified"
    }
    scores = [_float(article.get("evidence_score")) for article in eligible_articles]
    valid_grounding = bool(allowed_ids) and not invalid_grounding and len(eligible_articles) > 0
    return {
        "citation_count": len(eligible_articles),
        "independent_source_count": len(sources),
        "minimum_evidence_score": round(min(score for score in scores if score is not None), 4) if scores else None,
        "valid_grounding": valid_grounding,
    }


def _technical_snapshot(market_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    history = market_context.get("underlying_price_history")
    history = history if isinstance(history, dict) else {}
    summary = history.get("technical_summary")
    summary = summary if isinstance(summary, dict) else {}
    indicators = history.get("technical_indicators")
    indicators = indicators if isinstance(indicators, dict) else {}
    sector_market = indicators.get("sector_market_relative_strength")
    return summary, indicators, sector_market if isinstance(sector_market, dict) else {}


def evaluate_evidence_continuation_gate(
    *,
    recommendation: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    market_context: dict[str, Any] | None = None,
    policy: EvidenceContinuationPolicy = DEFAULT_EVIDENCE_CONTINUATION_POLICY,
) -> dict[str, Any]:
    """Return an auditable, deterministic entry-eligibility decision."""
    recommendation = recommendation if isinstance(recommendation, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    market_context = market_context if isinstance(market_context, dict) else payload.get("market_context", {})
    market_context = market_context if isinstance(market_context, dict) else {}
    proposed_decision = _text(recommendation.get("decision"))
    evidence = _evidence_snapshot(payload, policy)
    summary, indicators, sector_market = _technical_snapshot(market_context)
    volume = indicators.get("relative_volume") if isinstance(indicators.get("relative_volume"), dict) else {}
    relative_volume = _float(volume.get("relative_volume_20d"))
    sector_state = _text(sector_market.get("state"))
    overextension = _text(summary.get("overextension_risk")) or "unknown"
    technical = {
        "trend_state": _text(summary.get("trend_state")) or "unknown",
        "relative_strength_confirmation": _text(summary.get("relative_strength_confirmation")) or "unknown",
        "volume_confirmation": _text(summary.get("volume_confirmation")) or "unknown",
        "overextension_risk": overextension,
        "relative_volume_20d": relative_volume,
        "sector_market_relative_strength": sector_market,
        "entry_quality": _text(
            summary.get("call_entry_quality") if proposed_decision == "call" else summary.get("put_entry_quality")
        ) or "unknown",
    }
    if not policy.enabled:
        return {
            "allowed": proposed_decision in {"call", "put"},
            "would_allow": None,
            "proposed_decision": proposed_decision or "neither",
            "eligible_decision": proposed_decision if proposed_decision in {"call", "put"} else "neither",
            "reason_codes": ["policy_disabled"],
            "evidence": evidence,
            "technical": technical,
            "policy_version": policy.policy_version,
            "enforcement_mode": "disabled",
        }

    reasons: list[str] = []
    if proposed_decision not in {"call", "put"}:
        reasons.append("manager_not_directional")
    if not evidence["valid_grounding"]:
        reasons.append("invalid_or_missing_grounded_evidence")
    if evidence["citation_count"] < policy.required_evidence_count:
        reasons.append("insufficient_evidence_count")
    if evidence["independent_source_count"] < policy.required_independent_sources:
        reasons.append("insufficient_independent_sources")
    if evidence["minimum_evidence_score"] is None:
        reasons.append("missing_evidence_score")

    if proposed_decision == "call":
        if technical["trend_state"] != "bullish":
            reasons.append("trend_not_bullish")
        if policy.require_stock_relative_strength and technical["relative_strength_confirmation"] not in {"strong", "mixed_positive"}:
            reasons.append("stock_relative_strength_not_confirmed")
        if policy.require_sector_outperformance and sector_state != "outperforming":
            reasons.append("sector_not_outperforming_market")
        if technical["entry_quality"] not in policy.allowed_call_entry_quality:
            reasons.append("call_entry_quality_not_confirmed")
    elif proposed_decision == "put":
        if technical["trend_state"] != "bearish":
            reasons.append("trend_not_bearish")
        if policy.require_stock_relative_strength and technical["relative_strength_confirmation"] != "weak":
            reasons.append("stock_relative_strength_not_confirmed")
        if policy.require_sector_outperformance and sector_state != "underperforming":
            reasons.append("sector_not_underperforming_market")
        if technical["entry_quality"] not in policy.allowed_put_entry_quality:
            reasons.append("put_entry_quality_not_confirmed")

    if relative_volume is None:
        reasons.append("missing_relative_volume")
    elif relative_volume < policy.min_relative_volume:
        reasons.append("relative_volume_below_minimum")
    if technical["volume_confirmation"] != "strong":
        reasons.append("volume_not_confirmed")

    overextension_rank = {"low": 0, "medium": 1, "high": 2, "unknown": 3}
    if overextension_rank.get(overextension, 3) > overextension_rank.get(policy.max_overextension, 1):
        reasons.append("chase_risk" if overextension == "high" else "missing_overextension_data")
    if policy.fail_closed_on_missing_data:
        required_states = (
            technical["trend_state"],
            technical["relative_strength_confirmation"],
            technical["volume_confirmation"],
            overextension,
            sector_state,
        )
        if any(state in {"", "unknown"} for state in required_states) and "missing_technical_data" not in reasons:
            reasons.append("missing_technical_data")

    reason_codes = list(dict.fromkeys(reasons))
    would_allow = proposed_decision in {"call", "put"} and not reason_codes
    allowed = would_allow or (
        policy.enforcement_mode == "shadow" and proposed_decision in {"call", "put"}
    )
    return {
        "allowed": allowed,
        "would_allow": would_allow,
        "proposed_decision": proposed_decision or "neither",
        "eligible_decision": proposed_decision if allowed else "neither",
        "reason_codes": reason_codes,
        "evidence": evidence,
        "technical": technical,
        "policy_version": policy.policy_version,
        "enforcement_mode": policy.enforcement_mode,
    }
