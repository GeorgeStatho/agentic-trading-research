from __future__ import annotations

"""Public helper entrypoints for the manager stage.

The manager stage needs two kinds of data:
1. The strategist evidence package assembled from stored research.
2. The live market/account context assembled from Alpaca.

This module intentionally stays small and delegates the market-specific work to
``agent_helpers.market_context`` so callers only need one stable import surface.
"""

import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers


bootstrap_agent_callers(load_env_file=True)

from StrategistPayloadBuilder import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)
from agent_helpers.market_context import (
    DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE,
    build_market_context,
)


__all__ = [
    "DEFAULT_FULL_ARTICLE_LIMIT",
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE",
    "DEFAULT_SUMMARY_ARTICLE_LIMIT",
    "build_manager_input",
    "build_market_context",
    "test_market_context",
]


def build_manager_input(
    company_identifier: str,
    *,
    start_time,
    end_time,
    max_age_days: int | None,
    summary_article_limit: int,
    full_article_limit: int,
    option_expiration_date: str | None,
    option_expiration_date_gte: str | None,
    option_expiration_date_lte: str | None,
    option_strike_price_gte: float | None,
    option_strike_price_lte: float | None,
    option_contract_limit_per_type: int,
) -> dict[str, Any]:
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
    option_market = market_context.get("option_market", {})
    account_state = market_context.get("account_state", {})

    return {
        "company": company,
        "diagnostics": {
            "stock_price_available": bool(stock_snapshot.get("available")),
            "stock_price_error": str(stock_snapshot.get("error") or ""),
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
