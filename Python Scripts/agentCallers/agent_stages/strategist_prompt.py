from __future__ import annotations

import json
from typing import Any

from services.option_dte_buckets import TIME_HORIZON_TO_DTE_BUCKET, get_dte_bucket


def _build_time_horizon_bucket_guidance_text() -> str:
    guidance_parts: list[str] = []
    for time_horizon, bucket_key in TIME_HORIZON_TO_DTE_BUCKET.items():
        bucket = get_dte_bucket(bucket_key)
        bucket_label = bucket.label if bucket is not None else bucket_key
        guidance_parts.append(f"{time_horizon} for setups that fit {bucket_label}")

    if not guidance_parts:
        return "use unclear when timing conflicts or the evidence is mostly indirect"
    if len(guidance_parts) == 1:
        return guidance_parts[0]
    return ", ".join(guidance_parts[:-1]) + f", and {guidance_parts[-1]}"


def payload_has_evidence(payload: dict[str, Any]) -> bool:
    view_total = 0
    for view in payload.get("views", {}).values():
        try:
            view_total += int(view.get("count") or 0)
        except (TypeError, ValueError):
            continue

    supporting_articles = payload.get("supporting_articles", {})
    article_summaries = supporting_articles.get("article_summaries", [])
    full_articles = supporting_articles.get("full_articles", [])
    return view_total > 0 or bool(article_summaries) or bool(full_articles)


def build_context_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    historical_price_data = payload.get("company", {}).get("historical_price_data", {})
    return {
        "view_counts": {
            key: int(value.get("count") or 0)
            for key, value in payload.get("views", {}).items()
            if isinstance(value, dict)
        },
        "article_summary_count": len(payload.get("supporting_articles", {}).get("article_summaries", [])),
        "full_article_count": len(payload.get("supporting_articles", {}).get("full_articles", [])),
        "historical_periods_available": sum(
            1
            for item in historical_price_data.values()
            if isinstance(item, dict) and item.get("available")
        ),
    }


def build_strategist_prompt(
    payload: dict[str, Any],
    *,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, str]:
    # Build the strategist prompt by weaving together company context, evidence summaries, and decision instructions.
    time_horizon_bucket_guidance = _build_time_horizon_bucket_guidance_text()
    default_system_prompt = (
        "You are an investment strategist deciding whether a company currently supports opening an options trade candidate on the underlying equity. "
        "Use only the supplied structured context. Do not invent facts, catalysts, prices, or risks that are not supported by the input. "
        "Treat upstream agent conclusions as signals, not truth. Weigh them against article evidence, evidence freshness, contradiction level, "
        "and the supplied 1d, 5d, 1mo, and 3mo historical price action. "
        "Your primary job is to judge whether there is a real directional thesis on the underlying company or stock. "
        "Use recent price action mainly to judge timing quality and volatility around the thesis, not to decide affordability, liquidity, account fit, or final execution permission. "
        "When trend_quality is present, treat 5d_sharpe, 1m_sharpe, and 3m_sharpe as supporting context about how smooth or choppy recent returns were, not as standalone trade triggers. "
        "Do not let trend_quality by itself override stronger signals such as fresh company news, evidence materiality, contradiction level, or clear catalyst timing. "
        "Your job is not to choose stock shares and not to select a specific option contract. "
        "Your job is to decide whether the company should be passed forward as: trade_candidate, watchlist, or do_not_trade. "
        "Keep the recommendation high-level and avoid relying on proprietary production thresholds or hidden tuning. "
        "Express the directional view twice: expected_stock_direction as up, down, or neutral; and preferred_option_direction as call, put, or neither. "
        "These fields must be internally consistent with the decision. If there is no actionable options setup, preferred_option_direction should usually be neither. "
        "Distinguish between evidence quality and setup quality: a company can have an interesting story but still be a poor trade setup. "
        "why_now must explain why the setup is actionable now, or why it is not actionable now. "
        "summary must be a short paragraph. thesis, catalyst, and risks must be concise lists of short strings. "
        "Return only valid JSON with a top-level key named recommendation. "
        "Do not include markdown fences, explanations, or extra keys."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)
    task_prompt = (
        "Evaluate the company using the provided structured context and return JSON matching the required schema.\n"
        "Focus first on whether there is a real evidence-backed directional thesis. Treat setup_quality and timing_clarity as secondary descriptors, not as the final execution permission decision.\n"
        "Use opportunist_rollup as the primary summarized evidence layer, and use the article-level views and supporting_articles as the grounding detail behind it.\n"
        "High-materiality direct company impacts should dominate the directional prior. Industry evidence can support or weaken that thesis, and sector evidence should mostly act as backdrop rather than lead the trade decision.\n"
        "Use impact_direction to set the directional prior, impact_magnitude as evidence strength rather than trade permission, materiality to judge whether the article really matters for this entity, and effect_type to weight direct above indirect.\n"
        "Use company trend_quality as supporting context for return smoothness, not as a trade trigger. Read 5d_sharpe as short-term momentum quality, 1m_sharpe as recent trend quality, and 3m_sharpe as medium-term trend quality.\n"
        "Use trend_quality to refine timing and confidence, but do not let it overrule stronger evidence from news, catalysts, or direct company impacts.\n"
        "Build time_horizon from the dominant weighted opportunist horizons rather than copying a single article. Use the configured strategist horizon labels, align them with the live DTE bucket mapping, and use unclear when timing conflicts or the evidence is mostly indirect.\n"
        "Set contradictions_present to true when meaningful bullish and bearish signals materially conflict, especially if direct high-materiality company evidence disagrees with itself or with the industry and sector rollups.\n"
        "Use top reasons and the most material direct evidence to write why_now, summary, thesis, catalyst, risks, and watchlist_reason. Prefer watchlist or do_not_trade when evidence is low-materiality, indirect-only, contradictory, weakly timed, or lacks peer differentiation.\n"
        "\n"
        "Field guidance:\n"
        "- decision: trade_candidate, watchlist, or do_not_trade\n"
        "- confidence: overall confidence in this recommendation\n"
        "- evidence_quality: strength and credibility of the supporting evidence; strong usually means multiple direct, high-materiality, internally consistent impacts\n"
        "- setup_quality: quality of the current trade setup for options, considering direction clarity and timing\n"
        "- timing_clarity: whether the expected move appears actionable now; use unclear when the opportunist horizons conflict or the timing signal is mostly indirect\n"
        "- preferred_option_direction: call, put, or neither\n"
        "- expected_stock_direction: up, down, or neutral\n"
        f"- time_horizon: expected time frame for the thesis to matter; use {time_horizon_bucket_guidance}\n"
        "- why_now: one short explanation of why the setup is actionable now, or why it is not\n"
        "- summary: short paragraph\n"
        "- catalyst: concrete possible drivers or near-term triggers from the input\n"
        "- thesis: strongest reasons supporting the recommendation\n"
        "- risks: strongest reasons the trade could fail or should be avoided\n"
        "- contradictions_present: true if meaningful conflicting evidence exists\n"
        "- watchlist_reason: explain what is missing before this could become a trade_candidate; empty string if not applicable\n"
    )

    user_payload = {
        "task": str(task_override or task_prompt),
        "company": payload["company"],
        "peer_groups": payload.get("peer_groups", {}),
        "filters": payload.get("filters", {}),
        "opportunist_rollup": payload.get("opportunist_rollup", {}),
        "views": payload.get("views", {}),
        "supporting_articles": payload.get("supporting_articles", {}),
        "required_output": {
            "recommendation": {
                "decision": "trade_candidate|watchlist|do_not_trade",
                "confidence": "high|medium|low",
                "evidence_quality": "strong|moderate|weak",
                "setup_quality": "strong|moderate|weak",
                "timing_clarity": "clear|unclear",
                "preferred_option_direction": "call|put|neither",
                "expected_stock_direction": "up|down|neutral",
                "time_horizon": "very_short_term|short_term|medium_term|unclear",
                "why_now": "short explanation",
                "summary": "short paragraph",
                "catalyst": ["short point"],
                "thesis": ["short point"],
                "risks": ["short point"],
                "contradictions_present": True,
                "watchlist_reason": "short explanation or empty string",
            }
        },
    }
    return system_prompt, json.dumps(user_payload, ensure_ascii=True)


def build_no_evidence_result(company: dict[str, Any], *, context_snapshot: dict[str, Any]) -> dict[str, Any]:
    # Assemble the no evidence result so callers can work from one normalized shape.
    return {
        "company": company,
        "context_snapshot": context_snapshot,
        "recommendation": {
            "decision": "do_not_trade",
            "confidence": "low",
            "evidence_quality": "weak",
            "setup_quality": "weak",
            "timing_clarity": "unclear",
            "preferred_option_direction": "neither",
            "expected_stock_direction": "neutral",
            "time_horizon": "unclear",
            "why_now": "There is not enough processed evidence to justify an actionable near-term options setup.",
            "summary": "There was not enough processed article evidence available to support opening an options trade on this underlying.",
            "catalyst": [],
            "thesis": [],
            "risks": ["Insufficient supporting evidence from macro, sector, industry, and company article analysis."],
            "contradictions_present": False,
            "watchlist_reason": "",
        },
    }
