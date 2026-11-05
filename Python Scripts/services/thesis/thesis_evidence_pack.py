from __future__ import annotations

"""Phase 1 of the LLM thesis validator: deterministic evidence pack builder.

Per position, per day, assemble a fixed-shape payload from data the system
already collects: the stored thesis, the position-manager snapshot, daily bars
(underlying, sector ETF, index), and ticker news scraped since entry.

Deliberately excluded so the adjudicator cannot anchor on them: option entry
price, current P&L, position size, and any account/portfolio state.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from services.core.numbers import safe_float


LOGGER = logging.getLogger(__name__)

EVIDENCE_PACK_VERSION = "thesis-evidence-pack-v1"
DEFAULT_NEWS_LIMIT = 10
_NEWS_SUMMARY_MAX_CHARS = 700
INDEX_BENCHMARK_SYMBOL = "SPY"


def _parse_timestamp(value: Any) -> datetime | None:
    from agent_helpers.text_utils import parse_published_at

    return parse_published_at(str(value or ""))


def _window_move_pct(
    bars: list[Any],
    *,
    since: datetime,
    extract_close: Callable[[Any], float | None],
    extract_timestamp: Callable[[Any], str],
) -> float | None:
    """Percent move from the last close at/before ``since`` to the latest close."""
    baseline_close: float | None = None
    latest_close: float | None = None
    for bar in bars:
        close = extract_close(bar)
        bar_at = _parse_timestamp(extract_timestamp(bar))
        if close is None or bar_at is None:
            continue
        if bar_at <= since:
            baseline_close = close
        latest_close = close
    if baseline_close in (None, 0) or latest_close is None:
        return None
    return round(((latest_close - baseline_close) / baseline_close) * 100.0, 4)


def _symbol_move_since(symbol: str, *, since: datetime) -> dict[str, Any]:
    from agent_helpers.market_context.underlying_bars import (
        _extract_bar_close,
        _extract_bar_timestamp,
        _fetch_daily_stock_bars,
    )

    bars, error = _fetch_daily_stock_bars(symbol)
    move_pct = (
        _window_move_pct(
            bars,
            since=since,
            extract_close=_extract_bar_close,
            extract_timestamp=_extract_bar_timestamp,
        )
        if bars
        else None
    )
    return {
        "symbol": symbol,
        "move_since_entry_pct": move_pct,
        "available": move_pct is not None,
        "error": error or ("No usable daily bars were returned." if move_pct is None else ""),
    }


def _resolve_sector_etf_symbol(thesis: dict[str, Any]) -> str:
    from agent_helpers.market_context.equities import _resolve_company_sector_etf
    from market_db_modules.portfolio_risk import get_company_sector_key

    sector_key = get_company_sector_key(
        symbol=str(thesis.get("symbol") or "").strip().upper(),
        company_id=thesis.get("company_id"),
    )
    resolved = _resolve_company_sector_etf({"sector_key": sector_key})
    if resolved is None:
        return ""
    _resolved_key, etf_entry, _matched_on = resolved
    return str(etf_entry.get("ticker") or "").strip().upper()


def _moneyness(position_summary: dict[str, Any]) -> dict[str, Any]:
    underlying_price = safe_float(position_summary.get("underlying_stock_price"))
    strike = safe_float(position_summary.get("strike"))
    contract_type = str(position_summary.get("contract_type") or "").strip().lower()
    ratio = None
    description = "unknown"
    if underlying_price is not None and strike not in (None, 0):
        ratio = round(underlying_price / strike, 4)
        if contract_type == "call":
            description = "in_the_money" if ratio > 1.0 else "out_of_the_money"
        elif contract_type == "put":
            description = "in_the_money" if ratio < 1.0 else "out_of_the_money"
    return {
        "underlying_to_strike_ratio": ratio,
        "description": description,
    }


def _news_since_entry(
    thesis: dict[str, Any],
    *,
    since: datetime,
    news_limit: int,
) -> list[dict[str, Any]]:
    company_id = thesis.get("company_id")
    if not isinstance(company_id, int) or company_id <= 0:
        return []

    from agent_helpers.text_utils import published_at_in_window
    from news_db_modules.queries import list_company_news_articles

    articles = list_company_news_articles(company_id)
    seen_keys: set[str] = set()
    selected: list[dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        if not published_at_in_window(article.get("published_at"), start_time=since, end_time=None):
            continue
        dedupe_key = str(article.get("article_key") or "") or (
            f"{str(article.get('source') or '').strip().lower()}::{str(article.get('title') or '').strip().lower()}"
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        summary_text = " ".join(str(article.get("summary") or article.get("body") or "").split())
        selected.append(
            {
                "article_id": article.get("id"),
                "source": str(article.get("source") or "").strip(),
                "published_at": str(article.get("published_at") or "").strip(),
                "title": " ".join(str(article.get("title") or "").split()),
                "summary": summary_text[:_NEWS_SUMMARY_MAX_CHARS],
            }
        )

    # list_company_news_articles returns newest first; keep the most recent N.
    return selected[: max(1, int(news_limit))]


def build_thesis_evidence_pack(
    thesis: dict[str, Any],
    position_summary: dict[str, Any],
    *,
    news_limit: int = DEFAULT_NEWS_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the fixed-shape adjudicator payload for one open position."""
    now = now or datetime.now(timezone.utc)
    opened_at = _parse_timestamp(thesis.get("opened_at")) or now
    days_held = max(0, (now - opened_at).days)

    underlying_symbol = str(thesis.get("symbol") or "").strip().upper()
    underlying_move = _symbol_move_since(underlying_symbol, since=opened_at)
    index_move = _symbol_move_since(INDEX_BENCHMARK_SYMBOL, since=opened_at)
    sector_etf_symbol = _resolve_sector_etf_symbol(thesis)
    sector_move = (
        _symbol_move_since(sector_etf_symbol, since=opened_at)
        if sector_etf_symbol
        else {"symbol": "", "move_since_entry_pct": None, "available": False, "error": "No sector ETF mapping."}
    )

    idiosyncratic_move_pct = None
    if underlying_move["move_since_entry_pct"] is not None and sector_move["move_since_entry_pct"] is not None:
        idiosyncratic_move_pct = round(
            underlying_move["move_since_entry_pct"] - sector_move["move_since_entry_pct"], 4
        )

    news_items = _news_since_entry(thesis, since=opened_at, news_limit=news_limit)

    return {
        "version": EVIDENCE_PACK_VERSION,
        "as_of": now.isoformat(),
        "thesis": {
            "symbol": underlying_symbol,
            "structure": str(thesis.get("structure") or "").strip(),
            "opened_at": str(thesis.get("opened_at") or "").strip(),
            "days_held": days_held,
            "catalyst_type": str(thesis.get("catalyst_type") or "").strip(),
            "catalyst_summary": str(thesis.get("catalyst_summary") or "").strip(),
            "expected_horizon_days": thesis.get("expected_horizon_days"),
            "invalidation_conditions": [
                str(condition) for condition in (thesis.get("invalidation_conditions") or [])
            ],
        },
        "position_context": {
            "days_to_expiration": position_summary.get("days_to_expiration"),
            "moneyness": _moneyness(position_summary),
            "option_spread_pct": position_summary.get("option_spread_pct"),
            # Per-position IV history is not collected today, so IV path stays
            # explicitly unavailable rather than silently absent.
            "iv_path": {"available": False},
        },
        "price_path": {
            "underlying": underlying_move,
            "sector_etf": sector_move,
            "index": index_move,
            "idiosyncratic_move_pct": idiosyncratic_move_pct,
        },
        "news_since_entry": news_items,
        "allowed_evidence_article_ids": sorted(
            {
                item["article_id"]
                for item in news_items
                if isinstance(item.get("article_id"), int) and item["article_id"] > 0
            }
        ),
    }
