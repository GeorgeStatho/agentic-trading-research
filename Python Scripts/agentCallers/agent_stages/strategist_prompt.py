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
        "market_context.market_regime is deterministic cycle-level timing context. It cannot replace grounded evidence and cannot override the later execution policy. "
        "The grounding block is authoritative: cite only its article_ids and refer only to its company, industry, and sector relationships. "
        "Weigh the evidence, its freshness and the live context before deciding; the production decision heuristics are not part of the public demo. "
        "Your primary job is to judge whether there is a real directional thesis on the underlying company or stock. "
        "Your job is not to choose stock shares and not to select a specific option contract. "
        "Your job is to decide whether the company should be passed forward as: trade_candidate, watchlist, or do_not_trade. "
        "Express the directional view twice: expected_stock_direction as up, down, or neutral; and preferred_option_direction as call, put, or neither. "
        "These fields must be internally consistent with the decision. If there is no actionable options setup, preferred_option_direction should usually be neither. "
        "why_now must explain why the setup is actionable now, or why it is not actionable now. "
        "summary must be a short paragraph. thesis, catalyst, and risks must be concise lists of short strings. "
        "Set grounded_company_symbol exactly to grounding.company.symbol. "
        "Set grounded_entity_ids exactly from grounding.company.id, grounding.industry.id, and grounding.sector.id. "
        "For every output claim, provide evidence_claims with a claim_type, a short claim, and one or more grounding.article_ids. "
        "Include one citation for summary and why_now, plus one citation for every catalyst, thesis, risk, and non-empty watchlist_reason item. "
        "Return only valid JSON with a top-level key named recommendation. "
        "Do not include markdown fences, explanations, or extra keys."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)
    task_prompt = (
        "Evaluate the company using the provided structured context and return JSON matching the required schema.\n"
        "Weigh the evidence, its freshness and the live context before deciding; the production decision heuristics are not part of the public demo.\n"
        "Use opportunist_rollup as the primary summarized evidence layer, and use the article-level views and supporting_articles as the grounding detail behind it.\n"
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
        "- grounded_company_symbol: exact value from grounding.company.symbol\n"
        "- grounded_entity_ids: exact company_id, industry_id, and sector_id from grounding\n"
        "- evidence_claims: cite only grounding.article_ids; include summary, why_now, and every catalyst/thesis/risk claim\n"
    )

    user_payload = {
        "task": str(task_override or task_prompt),
        "company": payload["company"],
        "peer_groups": payload.get("peer_groups", {}),
        "filters": payload.get("filters", {}),
        "opportunist_rollup": payload.get("opportunist_rollup", {}),
        "views": payload.get("views", {}),
        "supporting_articles": payload.get("supporting_articles", {}),
        "market_context": payload.get("market_context", {}),
        "grounding": payload.get("grounding", {}),
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
                "grounded_company_symbol": "exact grounded company symbol",
                "grounded_entity_ids": {
                    "company_id": 123,
                    "industry_id": 456,
                    "sector_id": 789,
                },
                "evidence_claims": [{
                    "claim_type": "summary|why_now|catalyst|thesis|risk|watchlist_reason",
                    "claim": "short grounded claim",
                    "evidence_article_ids": [123],
                }],
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
            "grounded_company_symbol": str(company.get("symbol") or "").strip().upper(),
            "grounded_entity_ids": {
                "company_id": company.get("company_id"),
                "industry_id": company.get("industry_id"),
                "sector_id": company.get("sector_id"),
            },
            "evidence_claims": [],
        },
    }
