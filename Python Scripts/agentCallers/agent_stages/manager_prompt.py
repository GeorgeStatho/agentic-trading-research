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
        "Treat the strategist recommendation as useful but not authoritative. "
        "The strategist may include decision, confidence, evidence_quality, setup_quality, timing_clarity, "
        "preferred_option_direction, expected_stock_direction, time_horizon, why_now, thesis, risks, and contradictions_present. "
        "Use that information as the upstream thesis input, then decide whether that thesis is tradable now under current conditions and constraints. "
        "Your primary job is execution permission: decide whether the setup should be acted on now, given live market context, account context, and timing. "
        "If the article and research evidence is strong, fresh, specific, and internally consistent, prefer that evidence over ordinary one-day market noise, intraday weakness, or broad volatility. "
        "Use opportunist_rollup as the primary summarized evidence layer, and use the compact views summary and supporting article summaries only as grounding detail. "
        "Use the live market context primarily to judge timing, tradability, current risk, account constraints, and whether the setup is too volatile right now. "
        "When volatility context is present, explicitly use it as a timing and tradeability signal. "
        "Pay attention to implied volatility percentile, IV versus historical volatility, and the short-term versus longer-term IV term structure. "
        "Low or normal IV percentile is favorable for buying options, while elevated or very high IV percentile should reduce enthusiasm and can justify lower confidence or no trade. "
        "If IV is far above historical volatility, treat premium as expensive; if IV/HV is very stretched, be more cautious about approving an options entry even when the directional thesis is good. "
        "If short-dated IV is much higher than longer-dated IV, treat that as event pricing or near-term risk; that should make you less comfortable with very short-dated exposure and can justify preferring a longer target_dte_bucket or choosing neither if timing looks poor. "
        "Use these volatility signals to adjust timing confidence and risk tolerance, but do not infer a specific contract from them. "
        "Do not let a weak daily move by itself overturn a very convincing bullish or bearish evidence-based thesis unless the live context clearly signals a meaningful contradiction or an unacceptably risky setup. "
        "Be willing to take a reasonable amount of risk when the opportunity looks genuinely strong, especially when the evidence is clear and timing is still actionable. "
        "Your job is only to choose call, put, or neither for the underlying at this time. "
        "Do not choose, rank, or infer a specific option contract. "
        "Do not make the final affordability or liquidity decision yourself. Contract affordability and liquidity are enforced later by the deterministic selector and risk layer. "
        "Choose 'call' only when the evidence is convincingly bullish, timing is sufficiently actionable for options, "
        "and account/market context can support opening the trade. "
        "Choose 'put' only when the evidence is convincingly bearish, timing is sufficiently actionable for options, "
        "and account/market context can support opening the trade. "
        "If the setup looks strong and the risks appear manageable, lean toward call or put rather than defaulting to neither. "
        "Prefer 'neither' when evidence is weak, stale, contradictory, directionally unclear, timing is unclear, "
        "setup quality is weak, or account constraints make the trade unattractive. "
        "If the strategist says watchlist, weak setup, unclear timing, or contradictions_present=true, that should usually push toward 'neither' "
        "unless the full context strongly supports immediate action anyway. "
        f"Map time_horizon to target_dte_bucket explicitly when a trade is supported using this shared mapping: {TIME_HORIZON_DTE_MAPPING_TEXT}. "
        f"Prefer the default bucket {DEFAULT_MANAGER_TARGET_DTE_BUCKET} when the setup looks actionable but there is no strong reason to force a shorter bucket. "
        "If no trade should be opened, or no clear horizon applies, use none. "
        "Return only valid JSON with a top-level key named 'recommendation'. "
        "The recommendation object must contain: decision, confidence, target_dte_bucket, reason. "
        "decision must be one of: call, put, neither. "
        "confidence must be one of: high, medium, low. "
        f"target_dte_bucket must be one of: {TARGET_DTE_BUCKET_CHOICES_TEXT}. "
        "Use 'none' when no options trade should be opened or when no DTE preference should be expressed. "
        "reason must be a short paragraph explaining the main drivers of the decision."
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
        "strategist_recommendation": _build_manager_visible_strategist_context(payload),
        "market_context": _build_manager_visible_market_context(payload),
        "required_output": {
            "recommendation": {
                "decision": "call|put|neither",
                "confidence": "high|medium|low",
                "target_dte_bucket": TARGET_DTE_BUCKET_CHOICES_TEXT,
                "reason": "short paragraph",
            }
        },
    }
    return system_prompt, json.dumps(user_payload, ensure_ascii=True)
