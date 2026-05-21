from __future__ import annotations

"""Facade for market-context helpers used by the manager stage.

The implementation lives in smaller sibling modules so the public import
surface can stay stable while the internals remain easier to navigate.
"""

import argparse
import json
from datetime import datetime
from typing import Any

from . import iv_history as _iv_history
from .account import _build_account_state, _serialize_position
from .common import (
    DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE,
    IV_PERCENTILE_DTE_BUCKETS,
    LOGGER,
    OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES,
    OPTION_IV_HISTORY_MAX_ENTRIES,
    OPTION_IV_HISTORY_PATH,
)
from .equities import (
    _build_current_stock_price_snapshot,
    _build_market_indices_snapshot,
    _build_sector_etf_snapshot,
    _build_stock_fallback_snapshot,
    _empty_market_index_snapshot,
    _get_reference_stock_price_from_snapshot,
    _load_sector_etf_map,
    _resolve_company_sector_etf,
)
from .option_chain import (
    _build_contract_request,
    _build_contract_request_debug,
    _build_option_market_snapshot,
    _contract_preference_key,
    _extract_contract_items,
    _fetch_option_contracts,
    _format_strike_filter,
    _normalize_contract_type,
    _normalize_option_snapshot_map,
    _select_contract_subset_near_reference,
    _serialize_greeks,
    _serialize_option_contract,
    _serialize_quote_snapshot,
)
from .underlying import (
    _build_underlying_price_history_snapshot as _build_underlying_price_history_snapshot_impl,
    _empty_underlying_price_history_snapshot,
    _extract_bar_close,
    _extract_bar_timestamp,
    _normalize_stock_bars_response,
    _summarize_underlying_price_history as _summarize_underlying_price_history_impl,
)


__all__ = [
    "DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE",
    "build_market_context",
]


def _load_option_iv_history_store() -> dict[str, Any]:
    return _iv_history._load_option_iv_history_store(OPTION_IV_HISTORY_PATH)


def _save_option_iv_history_store(store: dict[str, Any]) -> None:
    _iv_history._save_option_iv_history_store(store, OPTION_IV_HISTORY_PATH)


def _normalize_iv_history_entries(entries: Any) -> list[dict[str, Any]]:
    return _iv_history._normalize_iv_history_entries(
        entries,
        max_entries=OPTION_IV_HISTORY_MAX_ENTRIES,
    )


def _upsert_iv_history_entry(
    entries: list[dict[str, Any]],
    *,
    recorded_at: str,
    value: float,
) -> list[dict[str, Any]]:
    return _iv_history._upsert_iv_history_entry(
        entries,
        recorded_at=recorded_at,
        value=value,
        max_entries=OPTION_IV_HISTORY_MAX_ENTRIES,
    )


def _calculate_days_to_expiration(expiration_date: str | None) -> int | None:
    return _iv_history._calculate_days_to_expiration(expiration_date)


def _resolve_iv_percentile_dte_bucket(days_to_expiration: int | None) -> str | None:
    return _iv_history._resolve_iv_percentile_dte_bucket(days_to_expiration)


def _summarize_iv_by_dte_bucket(
    contracts: list[dict[str, Any]],
    *,
    reference_stock_price: float | None,
) -> dict[str, dict[str, Any]]:
    return _iv_history._summarize_iv_by_dte_bucket(
        contracts,
        reference_stock_price=reference_stock_price,
    )


def _annotate_contract_iv_percentiles(
    contracts: list[dict[str, Any]],
    *,
    bucket_percentiles: dict[str, float | None],
    bucket_history_counts: dict[str, int],
) -> None:
    _iv_history._annotate_contract_iv_percentiles(
        contracts,
        bucket_percentiles=bucket_percentiles,
        bucket_history_counts=bucket_history_counts,
        min_samples=OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES,
    )


def _historical_values_from_iv_entries(entries: list[dict[str, Any]]) -> list[float]:
    return _iv_history._historical_values_from_iv_entries(
        entries,
        max_entries=OPTION_IV_HISTORY_MAX_ENTRIES,
    )


def _record_option_iv_history(
    underlying_symbol: str,
    *,
    atm_mean_iv: float | None,
    short_term_atm_iv: float | None,
    longer_term_atm_iv: float | None,
    bucket_atm_ivs: dict[str, float] | None,
    recorded_at: str,
) -> dict[str, Any]:
    return _iv_history._record_option_iv_history(
        underlying_symbol,
        atm_mean_iv=atm_mean_iv,
        short_term_atm_iv=short_term_atm_iv,
        longer_term_atm_iv=longer_term_atm_iv,
        bucket_atm_ivs=bucket_atm_ivs,
        recorded_at=recorded_at,
        path=OPTION_IV_HISTORY_PATH,
        max_entries=OPTION_IV_HISTORY_MAX_ENTRIES,
    )


def _enrich_with_alpaca_iv_percentiles(
    underlying_symbol: str,
    volatility_summary: dict[str, Any],
    *,
    contracts: list[dict[str, Any]] | None = None,
    reference_stock_price: float | None = None,
) -> dict[str, Any]:
    return _iv_history._enrich_with_alpaca_iv_percentiles(
        underlying_symbol,
        volatility_summary,
        contracts=contracts,
        reference_stock_price=reference_stock_price,
        path=OPTION_IV_HISTORY_PATH,
        max_entries=OPTION_IV_HISTORY_MAX_ENTRIES,
        min_samples=OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES,
        current_datetime=datetime,
    )


def _summarize_underlying_price_history(
    symbol: str,
    bars: list[Any],
    *,
    source: str = "alpaca_stock_bars",
) -> dict[str, Any]:
    return _summarize_underlying_price_history_impl(symbol, bars, source=source)


def _build_underlying_price_history_snapshot(symbol: str) -> dict[str, Any]:
    return _build_underlying_price_history_snapshot_impl(symbol)


def build_market_context(
    company: dict[str, Any],
    *,
    option_expiration_date: str | None,
    option_expiration_date_gte: str | None,
    option_expiration_date_lte: str | None,
    option_strike_price_gte: float | None,
    option_strike_price_lte: float | None,
    option_contract_limit_per_type: int,
) -> dict[str, Any]:
    company_symbol = str(company.get("symbol") or "").strip().upper()
    stock_snapshot = _build_current_stock_price_snapshot(company)
    reference_stock_price = _get_reference_stock_price_from_snapshot(stock_snapshot)
    underlying_price_history = _build_underlying_price_history_snapshot(company_symbol)
    return {
        "current_stock_price": stock_snapshot,
        "underlying_price_history": underlying_price_history,
        "market_indices": _build_market_indices_snapshot(),
        "sector_etf": _build_sector_etf_snapshot(company),
        "option_market": _build_option_market_snapshot(
            company_symbol,
            reference_stock_price=reference_stock_price,
            underlying_price_history=underlying_price_history,
            expiration_date=option_expiration_date,
            expiration_date_gte=option_expiration_date_gte,
            expiration_date_lte=option_expiration_date_lte,
            strike_price_gte=option_strike_price_gte,
            strike_price_lte=option_strike_price_lte,
            contract_limit_per_type=option_contract_limit_per_type,
        ),
        "account_state": _build_account_state(company_symbol),
    }


def _run_reference_price_smoke_test(symbol: str) -> int:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        print("Usage: python market_context.py <SYMBOL>")
        return 1

    stock_snapshot = _build_current_stock_price_snapshot({"symbol": normalized_symbol})
    reference_stock_price = _get_reference_stock_price_from_snapshot(stock_snapshot)
    print(f"symbol: {normalized_symbol}")
    print(f"snapshot_source: {stock_snapshot.get('source')}")
    print(f"snapshot_timestamp: {stock_snapshot.get('timestamp')}")
    print(f"latest_trade_timestamp: {stock_snapshot.get('latest_trade_timestamp')}")
    print(f"latest_trade_price: {stock_snapshot.get('latest_trade_price')}")
    print(f"price: {stock_snapshot.get('price')}")
    print(f"bid_price: {stock_snapshot.get('bid_price')}")
    print(f"ask_price: {stock_snapshot.get('ask_price')}")
    print(f"midpoint_price: {stock_snapshot.get('midpoint_price')}")
    print(f"reference_stock_price: {reference_stock_price}")
    print(f"available: {stock_snapshot.get('available')}")
    print(f"error: {stock_snapshot.get('error', '')}")
    print("snapshot_json:")
    print(json.dumps(stock_snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Smoke-test the Alpaca-backed stock reference price calculation."
    )
    parser.add_argument("symbol", nargs="?", help="Ticker symbol to inspect, e.g. SHOP")
    args = parser.parse_args()
    raise SystemExit(_run_reference_price_smoke_test(args.symbol or "AAPL"))
