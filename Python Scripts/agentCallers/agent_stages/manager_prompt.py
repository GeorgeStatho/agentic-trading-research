from __future__ import annotations

import json
from typing import Any

from .manager_payload_context import (
    _build_context_snapshot,
    _build_manager_visible_market_context,
    _build_manager_visible_supporting_articles,
    _build_manager_visible_views_summary,
)
from .manager_strategist_context import _build_manager_visible_strategist_context
from .manager_shared import (
    DEFAULT_MANAGER_TARGET_DTE_BUCKET,
    TARGET_DTE_BUCKET_CHOICES_TEXT,
    TIME_HORIZON_DTE_MAPPING_TEXT,
)


def build_manager_prompt(
    payload: dict[str, Any],
    *,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, str]:
    default_system_prompt = (
        "You are an investment manager deciding whether a company currently supports a bullish options call, "
        "a bearish options put, or neither. "
        "Use only the supplied structured context. "
        "You are reviewing: "
        "1) the structured research package, including summarized opportunist evidence, compact supporting article summaries, and 1d, 5d, 1mo, and 3mo price history; "
        "2) live market and account context, including current stock price, relevant sector ETF performance, broad market indexes, option market summary, buying power, and current position state; "
        "and 3) the strategist recommendation as an upstream signal. "
        "When recent same-company manager decision history is present, use it as reflective feedback about what was tried before, what evidence was used, and how those decisions performed, but do not let one recent outcome override stronger fresh evidence. "
        "Resolved autopsy insights are compact outcome feedback from prior decisions, not new market evidence. Use them only to calibrate confidence, timing, DTE caution, and recurring risk patterns. Do not cite their historical article IDs as support for the current thesis, and never let them override current grounded evidence or deterministic risk gates. "
        "Treat the strategist recommendation as useful but not authoritative. "
        "The strategist may include decision, confidence, evidence_quality, setup_quality, timing_clarity, "
        "preferred_option_direction, expected_stock_direction, time_horizon, why_now, thesis, risks, contradictions_present, "
        "grounded entity IDs, per-claim evidence_article_ids, and a strategist run reference. "
        "Use citations only when they reference articles in this manager payload; the run reference is review metadata, not evidence by itself. "
        "Use that information as the upstream thesis input, then decide whether that thesis is tradable now under current conditions and constraints. "
        "Your primary job is execution permission: decide whether the setup should be acted on now, given live market context, account context, and timing. "
        "Weigh the evidence, its freshness and the live context before deciding; the production decision heuristics are not part of the public demo. "
        "Use opportunist_rollup as the primary summarized evidence layer, and use the compact views summary and supporting article summaries only as grounding detail. "
        "market_context.market_regime is deterministic cycle-level timing context. It cannot replace grounded evidence and its later execution policy cannot be overridden by this recommendation. "
        "Your job is only to choose call, put, or neither for the underlying at this time. "
        "Do not choose, rank, or infer a specific option contract. "
        "Do not make the final affordability or liquidity decision yourself. Contract affordability and liquidity are enforced later by the deterministic selector and risk layer. "
        f"Map time_horizon to target_dte_bucket explicitly when a trade is supported using this shared mapping: {TIME_HORIZON_DTE_MAPPING_TEXT}. "
        f"Prefer the default bucket {DEFAULT_MANAGER_TARGET_DTE_BUCKET} when the setup looks actionable but there is no strong reason to force a shorter bucket. "
        "If no trade should be opened, or no clear horizon applies, use none. "
        "Return only valid JSON with a top-level key named 'recommendation'. "
        "The recommendation object must contain: decision, confidence, target_dte_bucket, technical_confirmation, entry_quality, recommended_action_adjustment, and reason. "
        "decision must be one of: call, put, neither. "
        "confidence must be one of: high, medium, low. "
        f"target_dte_bucket must be one of: {TARGET_DTE_BUCKET_CHOICES_TEXT}. "
        "technical_confirmation must be one of: strong, moderate, mixed, weak, overextended. "
        "entry_quality must be one of: excellent, good, acceptable, poor, chase. "
        "recommended_action_adjustment must be one of: approve, approve_with_normal_size, reduce_confidence, reduce_size, wait, skip. "
        "reason must be a short paragraph explaining the main drivers of the decision. "
        "Also include these optional but preferred fields when supported by the context: overextension_risk, options_pricing_risk, technical_summary, and risk_summary."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)

    user_payload = {
        "task": str(
            task_override
            or "Decide whether the supplied strategist thesis is tradable now as a call, put, or neither under current market and account constraints."
        ),
        "company": payload["company"],
        "filters": payload.get("filters", {}),
        "context_snapshot": _build_context_snapshot(payload),
        "opportunist_rollup": payload.get("opportunist_rollup", {}),
        "views_summary": _build_manager_visible_views_summary(payload),
        "supporting_articles": _build_manager_visible_supporting_articles(payload),
        "recent_manager_decision_history": payload.get("recent_manager_decision_history", []),
        "resolved_autopsy_insights": payload.get("resolved_autopsy_insights", []),
        "strategist_recommendation": _build_manager_visible_strategist_context(payload),
        "market_context": _build_manager_visible_market_context(payload),
        "required_output": {
            "recommendation": {
                "decision": "call|put|neither",
                "confidence": "high|medium|low",
                "target_dte_bucket": TARGET_DTE_BUCKET_CHOICES_TEXT,
                "technical_confirmation": "strong|moderate|mixed|weak|overextended",
                "entry_quality": "excellent|good|acceptable|poor|chase",
                "recommended_action_adjustment": "approve|approve_with_normal_size|reduce_confidence|reduce_size|wait|skip",
                "overextension_risk": "low|medium|high",
                "options_pricing_risk": "low|medium|high",
                "technical_summary": "short paragraph",
                "risk_summary": "short paragraph",
                "reason": "short paragraph",
            }
        },
    }
    return system_prompt, json.dumps(user_payload, ensure_ascii=True)
