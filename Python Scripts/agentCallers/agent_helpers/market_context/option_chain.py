from __future__ import annotations

from datetime import date
from typing import Any

from .common import (
    ALPACA_IMPORT_ERROR,
    CLOSEST_EXPIRATION_GTE,
    ContractType,
    DEFAULT_OPTION_CHAIN_FETCH_MULTIPLIER,
    DEFAULT_OPTION_FETCH_MIN,
    FARTHEST_EXPIRATION_LTE,
    GetOptionContractsRequest,
    LOGGER,
    OptionChainRequest,
    PREFERRED_OTM_DISTANCE,
    _env_flag,
    _get_alpaca_clients,
    _get_field,
    _safe_float,
    _serialize_scalar,
    summarize_option_iv,
)
from .iv_history import (
    _calculate_days_to_expiration,
    _enrich_with_alpaca_iv_percentiles,
    _resolve_iv_percentile_dte_bucket,
)


def _extract_contract_items(contracts_response: Any) -> list[Any]:
    if isinstance(contracts_response, dict):
        contract_items = contracts_response.get("option_contracts", [])
    else:
        contract_items = getattr(contracts_response, "option_contracts", contracts_response)
    if isinstance(contract_items, list):
        return contract_items
    try:
        return list(contract_items)
    except TypeError:
        return []


def _serialize_greeks(greeks: Any) -> dict[str, float | None]:
    return {
        "delta": _safe_float(_get_field(greeks, "delta")),
        "gamma": _safe_float(_get_field(greeks, "gamma")),
        "theta": _safe_float(_get_field(greeks, "theta")),
        "vega": _safe_float(_get_field(greeks, "vega")),
        "rho": _safe_float(_get_field(greeks, "rho")),
    }


def _serialize_quote_snapshot(quote: Any) -> dict[str, Any]:
    bid_price = _safe_float(_get_field(quote, "bid_price"))
    ask_price = _safe_float(_get_field(quote, "ask_price"))
    midpoint_price = round((bid_price + ask_price) / 2.0, 4) if bid_price is not None and ask_price is not None else None
    return {
        "bid_price": bid_price,
        "ask_price": ask_price,
        "midpoint_price": midpoint_price,
        "bid_size": _serialize_scalar(_get_field(quote, "bid_size")),
        "ask_size": _serialize_scalar(_get_field(quote, "ask_size")),
        "timestamp": _serialize_scalar(_get_field(quote, "timestamp"))
        or _serialize_scalar(_get_field(quote, "ask_timestamp"))
        or _serialize_scalar(_get_field(quote, "bid_timestamp"))
        or "",
    }


def _serialize_option_contract(contract: Any) -> dict[str, Any]:
    expiration_date = _serialize_scalar(_get_field(contract, "expiration_date")) or ""
    days_to_expiration = _calculate_days_to_expiration(expiration_date)
    return {
        "contract_id": _serialize_scalar(_get_field(contract, "id")),
        "symbol": str(_get_field(contract, "symbol") or ""),
        "underlying_symbol": str(_get_field(contract, "underlying_symbol") or ""),
        "contract_type": _serialize_scalar(_get_field(contract, "type")) or "",
        "expiration_date": expiration_date,
        "days_to_expiration": days_to_expiration,
        "dte_bucket": _resolve_iv_percentile_dte_bucket(days_to_expiration),
        "strike_price": _safe_float(_get_field(contract, "strike_price")),
        "style": _serialize_scalar(_get_field(contract, "style")) or "",
        "status": _serialize_scalar(_get_field(contract, "status")) or "",
        "size": _serialize_scalar(_get_field(contract, "size")),
        "open_interest": _safe_float(_get_field(contract, "open_interest")),
        "open_interest_date": _serialize_scalar(_get_field(contract, "open_interest_date")) or "",
        "close_price": _safe_float(_get_field(contract, "close_price")),
    }


def _format_strike_filter(value: float | None) -> str | None:
    if value is None:
        return None
    normalized = round(float(value), 2)
    return f"{normalized:.2f}".rstrip("0").rstrip(".")


def _build_contract_request(
    *,
    company_symbol: str,
    contract_type: Any,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    limit: int,
) -> Any:
    return GetOptionContractsRequest(
        underlying_symbols=[company_symbol],
        root_symbol=company_symbol,
        type=contract_type,
        expiration_date=expiration_date,
        expiration_date_gte=expiration_date_gte,
        expiration_date_lte=expiration_date_lte,
        strike_price_gte=_format_strike_filter(strike_price_gte),
        strike_price_lte=_format_strike_filter(strike_price_lte),
        limit=limit,
    )


def _build_contract_request_debug(
    *,
    company_symbol: str,
    contract_type: Any,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    limit: int,
) -> dict[str, Any]:
    return {
        "underlying_symbols": [company_symbol],
        "root_symbol": company_symbol,
        "type": _serialize_scalar(contract_type),
        "expiration_date": expiration_date or "",
        "expiration_date_gte": expiration_date_gte or "",
        "expiration_date_lte": expiration_date_lte or "",
        "strike_price_gte": _format_strike_filter(strike_price_gte) or "",
        "strike_price_lte": _format_strike_filter(strike_price_lte) or "",
        "limit": limit,
        "paper": _env_flag("ALPACA_PAPER", True),
    }


def _normalize_contract_type(value: Any) -> str:
    contract_type = str(getattr(value, "value", value) or "").strip().lower()
    if "." in contract_type:
        contract_type = contract_type.rsplit(".", 1)[-1]
    return {
        "c": "call",
        "call_option": "call",
        "call option": "call",
        "calls": "call",
        "p": "put",
        "put_option": "put",
        "put option": "put",
        "puts": "put",
    }.get(contract_type, contract_type) if contract_type in {
        "c", "call_option", "call option", "calls", "p", "put_option", "put option", "puts", "call", "put"
    } else ""


def _contract_preference_key(contract: dict[str, Any], target_strike: float | None) -> tuple[float, str, float, int]:
    strike_price = _safe_float(contract.get("strike_price"))
    strike_distance = abs(strike_price - target_strike) if strike_price is not None and target_strike is not None else float("inf")
    expiration_date = str(contract.get("expiration_date") or "9999-12-31")
    open_interest = _safe_float(contract.get("open_interest"))
    return (
        strike_distance,
        expiration_date,
        -(open_interest if open_interest is not None else -1.0),
        int(contract.get("option_id") or 10**9),
    )


def _select_contract_subset_near_reference(
    contracts: list[dict[str, Any]],
    *,
    reference_stock_price: float | None,
    per_type_limit: int,
) -> list[dict[str, Any]]:
    if reference_stock_price is None:
        return []
    limit = max(1, int(per_type_limit))
    call_target = reference_stock_price + PREFERRED_OTM_DISTANCE
    put_target = reference_stock_price - PREFERRED_OTM_DISTANCE
    selected_contracts: list[dict[str, Any]] = []
    for normalized_type, strike_cmp, target in (
        ("call", lambda strike: strike >= reference_stock_price, call_target),
        ("put", lambda strike: strike <= reference_stock_price, put_target),
    ):
        all_contracts = [
            contract
            for contract in contracts
            if _normalize_contract_type(contract.get("contract_type")) == normalized_type
            and (_safe_float(contract.get("strike_price")) is not None)
            and strike_cmp(_safe_float(contract.get("strike_price")) or 0.0)
        ]
        preferred_contracts = [
            contract
            for contract in all_contracts
            if (
                (_safe_float(contract.get("strike_price")) or 0.0) >= target
                if normalized_type == "call"
                else (_safe_float(contract.get("strike_price")) or 0.0) <= target
            )
        ]
        candidates = preferred_contracts or all_contracts
        sort_key = (
            (lambda contract: _contract_preference_key(contract, target))
            if preferred_contracts
            else lambda contract: (
                abs((_safe_float(contract.get("strike_price")) or 0.0) - reference_stock_price),
                str(contract.get("expiration_date") or "9999-12-31"),
                -(_safe_float(contract.get("open_interest")) or -1.0),
                int(contract.get("option_id") or 10**9),
            )
        )
        selected_contracts.extend(sorted(candidates, key=sort_key)[:limit])
    return selected_contracts


def _fetch_option_contracts(
    company_symbol: str,
    *,
    contract_type: Any,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    limit: int,
) -> list[Any]:
    clients = _get_alpaca_clients()
    if clients is None or GetOptionContractsRequest is None:
        return []
    response = clients["trading"].get_option_contracts(
        _build_contract_request(
            company_symbol=company_symbol,
            contract_type=contract_type,
            expiration_date=expiration_date,
            expiration_date_gte=expiration_date_gte,
            expiration_date_lte=expiration_date_lte,
            strike_price_gte=strike_price_gte,
            strike_price_lte=strike_price_lte,
            limit=limit,
        )
    )
    return _extract_contract_items(response)


def _normalize_option_snapshot_map(chain_response: Any) -> dict[str, Any]:
    if isinstance(chain_response, dict):
        return {str(symbol): snapshot for symbol, snapshot in chain_response.items()}
    try:
        return {str(symbol): snapshot for symbol, snapshot in dict(chain_response).items()}
    except (TypeError, ValueError):
        return {}


def _build_option_market_snapshot(
    company_symbol: str,
    *,
    reference_stock_price: float | None,
    underlying_price_history: dict[str, Any] | None = None,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    contract_limit_per_type: int,
) -> dict[str, Any]:
    unavailable = {
        "available": False,
        "underlying_symbol": company_symbol,
        "selection_filters": {
            "expiration_date": expiration_date or "",
            "expiration_date_gte": expiration_date_gte or "",
            "expiration_date_lte": expiration_date_lte or "",
            "strike_price_gte": strike_price_gte,
            "strike_price_lte": strike_price_lte,
            "contract_limit_per_type": contract_limit_per_type,
        },
        "contract_count": 0,
        "available_expirations": [],
        "available_strikes": [],
        "contracts": [],
    }
    clients = _get_alpaca_clients()
    if clients is None or ContractType is None or OptionChainRequest is None:
        unavailable["error"] = (
            f"alpaca-py is unavailable: {ALPACA_IMPORT_ERROR}"
            if ALPACA_IMPORT_ERROR is not None
            else "Alpaca credentials were not configured."
        )
        return unavailable

    try:
        raw_fetch_limit = max(contract_limit_per_type * DEFAULT_OPTION_CHAIN_FETCH_MULTIPLIER, DEFAULT_OPTION_FETCH_MIN)
        effective_strike_price_gte = strike_price_gte if strike_price_gte is not None else (
            max(0.0, reference_stock_price - 25.0) if reference_stock_price is not None else None
        )
        effective_strike_price_lte = strike_price_lte if strike_price_lte is not None else (
            reference_stock_price + 25.0 if reference_stock_price is not None else None
        )
        effective_expiration_date = expiration_date
        effective_expiration_date_gte = expiration_date_gte
        effective_expiration_date_lte = expiration_date_lte
        if not effective_expiration_date and not effective_expiration_date_gte and not effective_expiration_date_lte:
            today = date.today()
            effective_expiration_date_gte = (today.fromordinal(today.toordinal() + CLOSEST_EXPIRATION_GTE)).isoformat()
            effective_expiration_date_lte = (today.fromordinal(today.toordinal() + FARTHEST_EXPIRATION_LTE)).isoformat()

        call_request_debug = _build_contract_request_debug(
            company_symbol=company_symbol,
            contract_type=ContractType.CALL,
            expiration_date=effective_expiration_date,
            expiration_date_gte=effective_expiration_date_gte,
            expiration_date_lte=effective_expiration_date_lte,
            strike_price_gte=effective_strike_price_gte,
            strike_price_lte=effective_strike_price_lte,
            limit=raw_fetch_limit,
        )
        put_request_debug = _build_contract_request_debug(
            company_symbol=company_symbol,
            contract_type=ContractType.PUT,
            expiration_date=effective_expiration_date,
            expiration_date_gte=effective_expiration_date_gte,
            expiration_date_lte=effective_expiration_date_lte,
            strike_price_gte=effective_strike_price_gte,
            strike_price_lte=effective_strike_price_lte,
            limit=raw_fetch_limit,
        )
        LOGGER.info("Option contract lookup for %s using call_request=%s put_request=%s", company_symbol, call_request_debug, put_request_debug)
        call_contracts = _fetch_option_contracts(
            company_symbol,
            contract_type=ContractType.CALL,
            expiration_date=effective_expiration_date,
            expiration_date_gte=effective_expiration_date_gte,
            expiration_date_lte=effective_expiration_date_lte,
            strike_price_gte=effective_strike_price_gte,
            strike_price_lte=effective_strike_price_lte,
            limit=raw_fetch_limit,
        )
        put_contracts = _fetch_option_contracts(
            company_symbol,
            contract_type=ContractType.PUT,
            expiration_date=effective_expiration_date,
            expiration_date_gte=effective_expiration_date_gte,
            expiration_date_lte=effective_expiration_date_lte,
            strike_price_gte=effective_strike_price_gte,
            strike_price_lte=effective_strike_price_lte,
            limit=raw_fetch_limit,
        )
    except Exception as exc:
        unavailable["error"] = str(exc)
        LOGGER.warning(
            "Option market lookup failed for %s with error=%s call_request=%s put_request=%s",
            company_symbol,
            unavailable["error"],
            locals().get("call_request_debug", {}),
            locals().get("put_request_debug", {}),
        )
        return unavailable

    contract_map = {
        str(_get_field(contract, "symbol") or "").strip().upper(): contract
        for contract in call_contracts + put_contracts
        if str(_get_field(contract, "symbol") or "").strip().upper()
    }
    if not contract_map:
        unavailable["error"] = (
            f"No matching option contracts were returned for {company_symbol} "
            f"within expiration window {effective_expiration_date or ''} "
            f"{effective_expiration_date_gte or ''} {effective_expiration_date_lte or ''}."
        )
        unavailable["selection_filters"] = {
            **unavailable["selection_filters"],
            "effective_expiration_date": effective_expiration_date or "",
            "effective_expiration_date_gte": effective_expiration_date_gte or "",
            "effective_expiration_date_lte": effective_expiration_date_lte or "",
            "effective_strike_price_gte": effective_strike_price_gte,
            "effective_strike_price_lte": effective_strike_price_lte,
        }
        unavailable["request_debug"] = {"call_request": call_request_debug, "put_request": put_request_debug}
        LOGGER.warning(
            "Option market returned no contracts for %s. option_market.error=%s call_request=%s put_request=%s",
            company_symbol,
            unavailable["error"],
            call_request_debug,
            put_request_debug,
        )
        return unavailable

    snapshots_by_symbol: dict[str, Any] = {}
    chain_error = ""
    try:
        chain_response = clients["option"].get_option_chain(
            OptionChainRequest(
                underlying_symbol=company_symbol,
                expiration_date=effective_expiration_date,
                expiration_date_gte=effective_expiration_date_gte,
                expiration_date_lte=effective_expiration_date_lte,
                strike_price_gte=effective_strike_price_gte,
                strike_price_lte=effective_strike_price_lte,
            )
        )
        snapshots_by_symbol = _normalize_option_snapshot_map(chain_response)
    except Exception as exc:
        chain_error = str(exc)

    serialized_contracts: list[dict[str, Any]] = []
    expiration_values: set[str] = set()
    strike_values: set[float] = set()
    for symbol, contract in contract_map.items():
        serialized = _serialize_option_contract(contract)
        snapshot = snapshots_by_symbol.get(symbol)
        if snapshot is not None:
            serialized["implied_volatility"] = _safe_float(_get_field(snapshot, "implied_volatility"))
            serialized["greeks"] = _serialize_greeks(_get_field(snapshot, "greeks"))
            serialized["latest_quote"] = _serialize_quote_snapshot(_get_field(snapshot, "latest_quote"))
            serialized["latest_trade_price"] = _safe_float(_get_field(_get_field(snapshot, "latest_trade"), "price"))
        else:
            serialized["implied_volatility"] = None
            serialized["greeks"] = _serialize_greeks(None)
            serialized["latest_quote"] = _serialize_quote_snapshot(None)
            serialized["latest_trade_price"] = None
        if serialized["expiration_date"]:
            expiration_values.add(str(serialized["expiration_date"]))
        if serialized["strike_price"] is not None:
            strike_values.add(float(serialized["strike_price"]))
        serialized_contracts.append(serialized)

    serialized_contracts.sort(
        key=lambda item: (
            str(item.get("expiration_date") or ""),
            float(item.get("strike_price") or 0),
            str(item.get("contract_type") or ""),
            str(item.get("symbol") or ""),
        )
    )
    for index, contract in enumerate(serialized_contracts, start=1):
        contract["option_id"] = index

    selected_contracts = _select_contract_subset_near_reference(
        serialized_contracts,
        reference_stock_price=reference_stock_price,
        per_type_limit=contract_limit_per_type,
    )
    payload = {
        "available": True,
        "underlying_symbol": company_symbol,
        "selection_filters": {
            **unavailable["selection_filters"],
            "effective_expiration_date": effective_expiration_date or "",
            "effective_expiration_date_gte": effective_expiration_date_gte or "",
            "effective_expiration_date_lte": effective_expiration_date_lte or "",
            "effective_strike_price_gte": effective_strike_price_gte,
            "effective_strike_price_lte": effective_strike_price_lte,
        },
        "reference_stock_price": reference_stock_price,
        "raw_contract_count": len(serialized_contracts),
        "contract_count": len(selected_contracts),
        "available_expirations": sorted(expiration_values),
        "available_strikes": sorted(strike_values),
        "contracts": selected_contracts,
        "volatility_summary": summarize_option_iv(
            serialized_contracts,
            reference_stock_price,
            historical_volatility=_safe_float((underlying_price_history or {}).get("historical_volatility_20d")),
        ),
        "request_debug": {
            "call_request": call_request_debug,
            "put_request": put_request_debug,
        },
    }
    payload["volatility_summary"] = _enrich_with_alpaca_iv_percentiles(
        company_symbol,
        payload["volatility_summary"],
        contracts=serialized_contracts,
        reference_stock_price=reference_stock_price,
    )
    payload["volatility_summary"].update(
        {
            "historical_volatility_20d": _safe_float((underlying_price_history or {}).get("historical_volatility_20d")),
            "historical_volatility_60d": _safe_float((underlying_price_history or {}).get("historical_volatility_60d")),
            "historical_volatility_source": str((underlying_price_history or {}).get("source") or ""),
            "underlying_close_count": int(_safe_float((underlying_price_history or {}).get("close_count")) or 0),
        }
    )
    if chain_error:
        payload["warning"] = f"Option chain snapshots were unavailable: {chain_error}"
    return payload
