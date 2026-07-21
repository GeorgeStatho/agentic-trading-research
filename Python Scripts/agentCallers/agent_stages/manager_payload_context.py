from __future__ import annotations

from typing import Any

from .manager_strategist_context import _build_manager_visible_strategist_context


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

    market_context = payload.get("market_context", {})
    stock_price_available = bool(market_context.get("current_stock_price", {}).get("available"))
    sector_etf_available = bool(market_context.get("sector_etf", {}).get("available"))
    option_contract_count = int(market_context.get("option_market", {}).get("contract_count") or 0)
    account_state_available = bool(market_context.get("account_state", {}).get("available"))

    return (
        view_total > 0
        or bool(article_summaries)
        or bool(full_articles)
        or stock_price_available
        or sector_etf_available
        or option_contract_count > 0
        or account_state_available
    )


def _build_context_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    market_context = payload.get("market_context", {})
    strategist_recommendation = _build_manager_visible_strategist_context(payload)
    market_indices = market_context.get("market_indices", {})
    return {
        "view_counts": {
            key: int(value.get("count") or 0)
            for key, value in payload.get("views", {}).items()
            if isinstance(value, dict)
        },
        "article_summary_count": len(payload.get("supporting_articles", {}).get("article_summaries", [])),
        "full_article_count": len(payload.get("supporting_articles", {}).get("full_articles", [])),
        "stock_price_available": bool(market_context.get("current_stock_price", {}).get("available")),
        "market_index_count_available": sum(
            1
            for snapshot in market_indices.values()
            if isinstance(snapshot, dict) and snapshot.get("available")
        ),
        "sector_etf_available": bool(market_context.get("sector_etf", {}).get("available")),
        "option_contract_count": int(market_context.get("option_market", {}).get("contract_count") or 0),
        "account_state_available": bool(market_context.get("account_state", {}).get("available")),
        "matching_position_count": int(
            market_context.get("account_state", {})
            .get("company_position_state", {})
            .get("matching_position_count")
            or 0
        ),
        "strategist_decision": str(strategist_recommendation.get("decision") or ""),
        "strategist_option_direction": str(strategist_recommendation.get("preferred_option_direction") or ""),
        "strategist_stock_direction": str(strategist_recommendation.get("expected_stock_direction") or ""),
        "strategist_evidence_quality": str(strategist_recommendation.get("evidence_quality") or ""),
        "strategist_setup_quality": str(strategist_recommendation.get("setup_quality") or ""),
        "strategist_timing_clarity": str(strategist_recommendation.get("timing_clarity") or ""),
        "strategist_time_horizon": str(strategist_recommendation.get("time_horizon") or ""),
        "strategist_contradictions_present": bool(strategist_recommendation.get("contradictions_present")),
        "resolved_autopsy_insight_count": len(payload.get("resolved_autopsy_insights", [])),
    }


def _build_manager_visible_market_context(payload: dict[str, Any]) -> dict[str, Any]:
    market_context = payload.get("market_context", {})
    option_market = market_context.get("option_market", {})
    return {
        "current_stock_price": market_context.get("current_stock_price", {}),
        "underlying_price_history": market_context.get("underlying_price_history", {}),
        "market_indices": market_context.get("market_indices", {}),
        "sector_etf": market_context.get("sector_etf", {}),
        "account_state": market_context.get("account_state", {}),
        "option_market_summary": {
            "available": bool(option_market.get("available")),
            "underlying_symbol": option_market.get("underlying_symbol"),
            "selection_filters": option_market.get("selection_filters", {}),
            "contract_count": int(option_market.get("contract_count") or 0),
            "available_expirations": option_market.get("available_expirations", []),
            "available_strikes": option_market.get("available_strikes", []),
            "volatility_summary": option_market.get("volatility_summary", {}),
            "error": str(option_market.get("error") or ""),
        },
    }


def _build_manager_visible_views_summary(payload: dict[str, Any]) -> dict[str, Any]:
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    summarized: dict[str, Any] = {}
    for key, value in payload.get("views", {}).items():
        if not isinstance(value, dict):
            continue
        raw_signals = value.get("agent_conclusions", [])
        compact_signals: list[dict[str, Any]] = []
        if isinstance(raw_signals, list):
            sorted_signals = sorted(
                (signal for signal in raw_signals if isinstance(signal, dict)),
                key=lambda signal: (
                    confidence_rank.get(str(signal.get("confidence") or "").strip().lower(), 0),
                    str(signal.get("published_at") or ""),
                    int(signal.get("article_id") or 0),
                ),
                reverse=True,
            )
            for signal in sorted_signals[:3]:
                compact_signals.append(
                    {
                        "article_id": int(signal.get("article_id") or 0),
                        "confidence": str(signal.get("confidence") or ""),
                        "reason": str(signal.get("reason") or ""),
                        "impact_direction": str(signal.get("impact_direction") or ""),
                        "impact_magnitude": str(signal.get("impact_magnitude") or ""),
                        "published_at": str(signal.get("published_at") or ""),
                        "title": str(signal.get("title") or ""),
                    }
                )
        summarized[key] = {
            "label": str(value.get("label") or ""),
            "count": int(value.get("count") or 0),
            "top_signals": compact_signals,
        }
    return summarized


def _build_manager_visible_supporting_articles(payload: dict[str, Any]) -> dict[str, Any]:
    supporting_articles = payload.get("supporting_articles", {})
    if not isinstance(supporting_articles, dict):
        return {"article_summaries": []}

    article_summaries = supporting_articles.get("article_summaries", [])
    if not isinstance(article_summaries, list):
        return {"article_summaries": []}

    compact_summaries: list[dict[str, Any]] = []
    for article in article_summaries[:5]:
        if not isinstance(article, dict):
            continue
        compact_summaries.append(
            {
                "article_id": int(article.get("article_id") or 0),
                "title": str(article.get("title") or ""),
                "summary": str(article.get("summary") or ""),
                "source": str(article.get("source") or ""),
                "published_at": str(article.get("published_at") or ""),
                "article_scope": str(article.get("article_scope") or ""),
                "evidence_layers": [
                    str(layer or "").strip()
                    for layer in article.get("evidence_layers", [])
                    if str(layer or "").strip()
                ],
            }
        )
    return {"article_summaries": compact_summaries}
