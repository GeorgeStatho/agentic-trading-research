from __future__ import annotations

"""Public helper entrypoints for the manager stage.

The manager stage needs two kinds of data:
1. The strategist evidence package assembled from stored research.
2. The live market/account context assembled from Alpaca.

This module intentionally stays small and delegates the market-specific work to
``agent_helpers.market_context`` so callers only need one stable import surface.
"""

import json
import os
from pathlib import Path
import sys
from typing import Any
from datetime import datetime

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers, load_project_env


if __name__ == "__main__":
    load_project_env()

bootstrap_agent_callers()

from agent_builders.strategist_payload import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)
from agent_helpers.market_context import (
    DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE,
    build_market_context,
)
from agent_contracts import ManagerInputPayload
from db_helpers.market import list_recent_manager_decision_history


DEFAULT_RECENT_MANAGER_HISTORY_LIMIT = max(1, int(os.getenv("MANAGER_DECISION_HISTORY_LIMIT", "3")))
DEFAULT_RECENT_MANAGER_HISTORY_MAX_AGE_DAYS = max(
    1,
    int(os.getenv("MANAGER_DECISION_HISTORY_MAX_AGE_DAYS", "30")),
)
DEFAULT_RECENT_MANAGER_HISTORY_ARTICLE_LIMIT = max(
    1,
    int(os.getenv("MANAGER_DECISION_HISTORY_ARTICLE_LIMIT", "3")),
)


__all__ = [
    "DEFAULT_FULL_ARTICLE_LIMIT",
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE",
    "DEFAULT_RECENT_MANAGER_HISTORY_ARTICLE_LIMIT",
    "DEFAULT_RECENT_MANAGER_HISTORY_LIMIT",
    "DEFAULT_RECENT_MANAGER_HISTORY_MAX_AGE_DAYS",
    "DEFAULT_SUMMARY_ARTICLE_LIMIT",
    "build_manager_input",
    "build_market_context",
    "test_market_context",
]


def _compact_article_reference(article: dict[str, Any]) -> dict[str, Any]:
    return {
        "article_id": int(article.get("article_id") or 0),
        "title": str(article.get("title") or ""),
        "source": str(article.get("source") or ""),
        "published_at": str(article.get("published_at") or ""),
        "article_scope": str(article.get("article_scope") or ""),
        "evidence_layers": [
            str(layer or "").strip()
            for layer in article.get("evidence_layers", [])
            if str(layer or "").strip()
        ],
    }


def _extract_recent_history_article_references(
    manager_input_json: Any,
    *,
    article_limit: int,
) -> list[dict[str, Any]]:
    if isinstance(manager_input_json, str):
        try:
            manager_input_json = json.loads(manager_input_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(manager_input_json, dict):
        return []

    article_references = manager_input_json.get("article_references")
    if isinstance(article_references, list):
        compact: list[dict[str, Any]] = []
        for item in article_references[: max(1, int(article_limit))]:
            if not isinstance(item, dict):
                continue
            compact.append(
                {
                    "article_id": int(item.get("article_id") or 0),
                    "title": str(item.get("title") or ""),
                    "source": str(item.get("source") or ""),
                    "published_at": str(item.get("published_at") or ""),
                    "article_scope": str(item.get("article_scope") or ""),
                    "evidence_layers": [
                        str(layer or "").strip()
                        for layer in item.get("evidence_layers", [])
                        if str(layer or "").strip()
                    ],
                }
            )
        return compact

    supporting_articles = manager_input_json.get("supporting_articles")
    if not isinstance(supporting_articles, dict):
        return []
    article_summaries = supporting_articles.get("article_summaries", [])
    if not isinstance(article_summaries, list):
        return []
    return [
        _compact_article_reference(article)
        for article in article_summaries[: max(1, int(article_limit))]
        if isinstance(article, dict)
    ]


def _build_recent_manager_decision_history(
    company: dict[str, Any],
    *,
    history_limit: int,
    history_max_age_days: int,
    history_article_limit: int,
) -> list[dict[str, Any]]:
    rows = list_recent_manager_decision_history(
        company_id=company.get("company_id"),
        underlying_symbol=company.get("symbol"),
        limit=max(1, int(history_limit)),
        within_days=max(1, int(history_max_age_days)),
    )
    summarized: list[dict[str, Any]] = []
    for row in rows:
        if row is None:
            continue
        item = dict(row)
        summarized.append(
            {
                "decision_run_at": str(item.get("decision_run_at") or ""),
                "manager_decision": str(item.get("manager_decision") or ""),
                "manager_confidence": str(item.get("manager_confidence") or ""),
                "target_dte_bucket": str(item.get("target_dte_bucket") or ""),
                "trade_executed": bool(item.get("trade_executed")),
                "latest_trade_pnl_pct": item.get("latest_trade_pnl_pct"),
                "resolved_outcome_label": str(item.get("resolved_outcome_label") or ""),
                "article_references": _extract_recent_history_article_references(
                    item.get("manager_input_json"),
                    article_limit=history_article_limit,
                ),
            }
        )
    return summarized


def build_manager_input(
    company_identifier: str,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
    summary_article_limit: int,
    full_article_limit: int,
    option_expiration_date: str | None,
    option_expiration_date_gte: str | None,
    option_expiration_date_lte: str | None,
    option_strike_price_gte: float | None,
    option_strike_price_lte: float | None,
    option_contract_limit_per_type: int,
    recent_history_limit: int = DEFAULT_RECENT_MANAGER_HISTORY_LIMIT,
    recent_history_max_age_days: int = DEFAULT_RECENT_MANAGER_HISTORY_MAX_AGE_DAYS,
    recent_history_article_limit: int = DEFAULT_RECENT_MANAGER_HISTORY_ARTICLE_LIMIT,
) -> ManagerInputPayload:
    """Build the full manager payload for one company.

    Usage:
        ``payload = build_manager_input("AAPL", start_time=None, end_time=None, ...)``

    The returned payload can be passed directly to
    ``agent_stages.manager.decide_company_option_position`` or inspected during
    local debugging.
    """
    payload = build_strategist_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
    )
    payload["market_context"] = build_market_context(
        payload["company"],
        option_expiration_date=option_expiration_date,
        option_expiration_date_gte=option_expiration_date_gte,
        option_expiration_date_lte=option_expiration_date_lte,
        option_strike_price_gte=option_strike_price_gte,
        option_strike_price_lte=option_strike_price_lte,
        option_contract_limit_per_type=max(1, int(option_contract_limit_per_type)),
    )
    payload["recent_manager_decision_history"] = _build_recent_manager_decision_history(
        payload["company"],
        history_limit=recent_history_limit,
        history_max_age_days=recent_history_max_age_days,
        history_article_limit=recent_history_article_limit,
    )
    return payload


def test_market_context(
    company_identifier: str,
    *,
    option_expiration_date: str | None = None,
    option_expiration_date_gte: str | None = None,
    option_expiration_date_lte: str | None = None,
    option_strike_price_gte: float | None = None,
    option_strike_price_lte: float | None = None,
    option_contract_limit_per_type: int = DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE,
) -> dict[str, Any]:
    """Return a diagnostics-first snapshot of manager market inputs.

    Usage:
        Run this helper when you want to confirm Alpaca credentials, option
        availability, and account state before involving the LLM stage.
    """
    payload = build_strategist_input(
        company_identifier,
        start_time=None,
        end_time=None,
        max_age_days=DEFAULT_MAX_ARTICLE_AGE_DAYS,
        summary_article_limit=DEFAULT_SUMMARY_ARTICLE_LIMIT,
        full_article_limit=DEFAULT_FULL_ARTICLE_LIMIT,
    )
    company = payload["company"]
    market_context = build_market_context(
        company,
        option_expiration_date=option_expiration_date,
        option_expiration_date_gte=option_expiration_date_gte,
        option_expiration_date_lte=option_expiration_date_lte,
        option_strike_price_gte=option_strike_price_gte,
        option_strike_price_lte=option_strike_price_lte,
        option_contract_limit_per_type=max(1, int(option_contract_limit_per_type)),
    )

    stock_snapshot = market_context.get("current_stock_price", {})
    market_indices = market_context.get("market_indices", {})
    sector_etf = market_context.get("sector_etf", {})
    option_market = market_context.get("option_market", {})
    account_state = market_context.get("account_state", {})

    return {
        "company": company,
        "diagnostics": {
            "stock_price_available": bool(stock_snapshot.get("available")),
            "stock_price_error": str(stock_snapshot.get("error") or ""),
            "market_index_count_available": sum(
                1
                for snapshot in market_indices.values()
                if isinstance(snapshot, dict) and snapshot.get("available")
            ),
            "sector_etf_available": bool(sector_etf.get("available")),
            "sector_etf_error": str(sector_etf.get("error") or ""),
            "option_market_available": bool(option_market.get("available")),
            "option_market_error": str(option_market.get("error") or ""),
            "option_contract_count": int(option_market.get("contract_count") or 0),
            "account_state_available": bool(account_state.get("available")),
            "account_state_error": str(account_state.get("error") or ""),
            "matching_position_count": int(
                account_state.get("company_position_state", {}).get("matching_position_count") or 0
            ),
        },
        "market_context": market_context,
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(json.dumps(test_market_context(target), ensure_ascii=True, indent=2))
