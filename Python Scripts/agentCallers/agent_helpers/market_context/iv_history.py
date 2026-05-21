from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .common import (
    IV_PERCENTILE_DTE_BUCKETS,
    LOGGER,
    OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES,
    OPTION_IV_HISTORY_MAX_ENTRIES,
    OPTION_IV_HISTORY_PATH,
    _safe_float,
    _serialize_scalar,
    compute_percentile_rank,
)


def _load_option_iv_history_store(path: Path = OPTION_IV_HISTORY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "underlyings": {}}
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "underlyings": {}}
    if not isinstance(raw_payload, dict):
        return {"version": 1, "underlyings": {}}
    underlyings = raw_payload.get("underlyings", {})
    return {
        "version": int(raw_payload.get("version") or 1),
        "underlyings": underlyings if isinstance(underlyings, dict) else {},
    }


def _save_option_iv_history_store(store: dict[str, Any], path: Path = OPTION_IV_HISTORY_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(store, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        LOGGER.warning("Failed to write option IV history to %s", path)


def _normalize_iv_history_entries(
    entries: Any,
    *,
    max_entries: int = OPTION_IV_HISTORY_MAX_ENTRIES,
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []
    normalized_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        recorded_at = str(entry.get("recorded_at") or "").strip()
        value = _safe_float(entry.get("value"))
        if not recorded_at or value is None:
            continue
        normalized_entries.append({"recorded_at": recorded_at, "value": round(value, 6)})
    normalized_entries.sort(key=lambda item: str(item["recorded_at"]))
    return normalized_entries[-max_entries:]


def _upsert_iv_history_entry(
    entries: list[dict[str, Any]],
    *,
    recorded_at: str,
    value: float,
    max_entries: int = OPTION_IV_HISTORY_MAX_ENTRIES,
) -> list[dict[str, Any]]:
    normalized_entries = _normalize_iv_history_entries(entries, max_entries=max_entries)
    for entry in normalized_entries:
        if str(entry.get("recorded_at") or "").strip() == str(recorded_at).strip():
            entry["recorded_at"] = recorded_at
            entry["value"] = round(value, 6)
            break
    else:
        normalized_entries.append({"recorded_at": recorded_at, "value": round(value, 6)})
    normalized_entries.sort(key=lambda item: str(item["recorded_at"]))
    return normalized_entries[-max_entries:]


def _calculate_days_to_expiration(expiration_date: str | None) -> int | None:
    expiration_text = str(expiration_date or "").strip()
    if not expiration_text:
        return None
    try:
        expiration = date.fromisoformat(expiration_text)
    except ValueError:
        return None
    return (expiration - date.today()).days


def _resolve_iv_percentile_dte_bucket(days_to_expiration: int | None) -> str | None:
    if days_to_expiration is None or days_to_expiration < 1:
        return None
    for bucket_key, min_days, max_days in IV_PERCENTILE_DTE_BUCKETS:
        if min_days <= days_to_expiration <= max_days:
            return bucket_key
    return None


def _summarize_iv_by_dte_bucket(
    contracts: list[dict[str, Any]],
    *,
    reference_stock_price: float | None,
) -> dict[str, dict[str, Any]]:
    grouped_contracts: dict[str, list[dict[str, Any]]] = {}
    for contract in contracts:
        bucket_key = str(contract.get("dte_bucket") or "").strip()
        implied_volatility = _safe_float(contract.get("implied_volatility"))
        if bucket_key and implied_volatility is not None and implied_volatility >= 0:
            grouped_contracts.setdefault(bucket_key, []).append(contract)

    bucket_summary: dict[str, dict[str, Any]] = {}
    for bucket_key, bucket_contracts in grouped_contracts.items():
        ranked_contracts = sorted(
            bucket_contracts,
            key=lambda contract: (
                abs(((_safe_float(contract.get("strike_price")) or reference_stock_price or 0.0) - (reference_stock_price or 0.0)))
                if reference_stock_price is not None
                else 0.0,
                str(contract.get("expiration_date") or "9999-12-31"),
                str(contract.get("contract_type") or ""),
                str(contract.get("symbol") or ""),
            ),
        )
        representative_ivs = [
            implied_volatility
            for implied_volatility in (
                _safe_float(contract.get("implied_volatility"))
                for contract in ranked_contracts[: min(4, len(ranked_contracts))]
            )
            if implied_volatility is not None
        ]
        if representative_ivs:
            bucket_summary[bucket_key] = {
                "atm_mean_iv": round(sum(representative_ivs) / len(representative_ivs), 6),
                "contract_count": len(bucket_contracts),
                "representative_contract_count": len(representative_ivs),
            }
    return bucket_summary


def _annotate_contract_iv_percentiles(
    contracts: list[dict[str, Any]],
    *,
    bucket_percentiles: dict[str, float | None],
    bucket_history_counts: dict[str, int],
    min_samples: int = OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES,
) -> None:
    for contract in contracts:
        days_to_expiration = contract.get("days_to_expiration")
        if days_to_expiration is None:
            days_to_expiration = _calculate_days_to_expiration(
                _serialize_scalar(contract.get("expiration_date")) or ""
            )
            contract["days_to_expiration"] = days_to_expiration

        bucket_key = str(contract.get("dte_bucket") or "").strip()
        if not bucket_key:
            bucket_key = _resolve_iv_percentile_dte_bucket(_safe_float(days_to_expiration))
            contract["dte_bucket"] = bucket_key

        bucket_percentile = bucket_percentiles.get(bucket_key)
        history_count = int(bucket_history_counts.get(bucket_key) or 0)
        contract["iv_percentile"] = bucket_percentile
        contract["dte_bucket_iv_percentile"] = bucket_percentile
        if not bucket_key:
            contract["iv_percentile_source"] = "no_dte_bucket"
        elif bucket_percentile is not None:
            contract["iv_percentile_source"] = f"dte_bucket:{bucket_key}"
        elif history_count < min_samples:
            contract["iv_percentile_source"] = "insufficient_bucket_history"
        else:
            contract["iv_percentile_source"] = f"dte_bucket:{bucket_key}_unavailable"


def _historical_values_from_iv_entries(
    entries: list[dict[str, Any]],
    *,
    max_entries: int = OPTION_IV_HISTORY_MAX_ENTRIES,
) -> list[float]:
    return [
        float(entry["value"])
        for entry in _normalize_iv_history_entries(entries, max_entries=max_entries)
        if _safe_float(entry.get("value")) is not None
    ]


def _record_option_iv_history(
    underlying_symbol: str,
    *,
    atm_mean_iv: float | None,
    short_term_atm_iv: float | None,
    longer_term_atm_iv: float | None,
    bucket_atm_ivs: dict[str, float] | None,
    recorded_at: str,
    path: Path = OPTION_IV_HISTORY_PATH,
    max_entries: int = OPTION_IV_HISTORY_MAX_ENTRIES,
) -> dict[str, Any]:
    store = _load_option_iv_history_store(path)
    underlyings = store.setdefault("underlyings", {})
    symbol_key = str(underlying_symbol or "").strip().upper()
    symbol_history = underlyings.get(symbol_key)
    if not isinstance(symbol_history, dict):
        symbol_history = {}

    for metric_name, metric_value in {
        "atm_mean_iv": atm_mean_iv,
        "short_term_atm_iv": short_term_atm_iv,
        "longer_term_atm_iv": longer_term_atm_iv,
    }.items():
        if metric_value is not None:
            symbol_history[metric_name] = _upsert_iv_history_entry(
                symbol_history.get(metric_name, []),
                recorded_at=recorded_at,
                value=metric_value,
                max_entries=max_entries,
            )

    bucket_histories = symbol_history.get("atm_mean_iv_by_dte_bucket", {})
    if not isinstance(bucket_histories, dict):
        bucket_histories = {}
    for bucket_key, bucket_value in (bucket_atm_ivs or {}).items():
        normalized_bucket_key = str(bucket_key or "").strip()
        if normalized_bucket_key:
            bucket_histories[normalized_bucket_key] = _upsert_iv_history_entry(
                bucket_histories.get(normalized_bucket_key, []),
                recorded_at=recorded_at,
                value=bucket_value,
                max_entries=max_entries,
            )
    symbol_history["atm_mean_iv_by_dte_bucket"] = bucket_histories
    symbol_history["updated_at"] = recorded_at
    underlyings[symbol_key] = symbol_history
    _save_option_iv_history_store(store, path)
    return symbol_history


def _enrich_with_alpaca_iv_percentiles(
    underlying_symbol: str,
    volatility_summary: dict[str, Any],
    *,
    contracts: list[dict[str, Any]] | None = None,
    reference_stock_price: float | None = None,
    path: Path = OPTION_IV_HISTORY_PATH,
    max_entries: int = OPTION_IV_HISTORY_MAX_ENTRIES,
    min_samples: int = OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES,
    current_datetime: type[datetime] = datetime,
) -> dict[str, Any]:
    if not isinstance(volatility_summary, dict):
        return {}

    recorded_at = current_datetime.now(timezone.utc).isoformat()
    term_structure = volatility_summary.get("term_structure", {})
    if not isinstance(term_structure, dict):
        term_structure = {}

    store = _load_option_iv_history_store(path)
    underlyings = store.get("underlyings", {})
    symbol_key = str(underlying_symbol or "").strip().upper()
    existing_symbol_history = underlyings.get(symbol_key, {})
    if not isinstance(existing_symbol_history, dict):
        existing_symbol_history = {}

    dte_bucket_iv_summary = _summarize_iv_by_dte_bucket(list(contracts or []), reference_stock_price=reference_stock_price)
    atm_mean_iv_history = _historical_values_from_iv_entries(existing_symbol_history.get("atm_mean_iv", []), max_entries=max_entries)
    short_term_iv_history = _historical_values_from_iv_entries(existing_symbol_history.get("short_term_atm_iv", []), max_entries=max_entries)
    longer_term_iv_history = _historical_values_from_iv_entries(existing_symbol_history.get("longer_term_atm_iv", []), max_entries=max_entries)
    existing_bucket_histories = existing_symbol_history.get("atm_mean_iv_by_dte_bucket", {})
    if not isinstance(existing_bucket_histories, dict):
        existing_bucket_histories = {}

    bucket_percentiles: dict[str, float | None] = {}
    bucket_history_counts: dict[str, int] = {}
    for bucket_key, bucket_summary in dte_bucket_iv_summary.items():
        bucket_history = _historical_values_from_iv_entries(existing_bucket_histories.get(bucket_key, []), max_entries=max_entries)
        bucket_history_counts[bucket_key] = len(bucket_history)
        bucket_percentiles[bucket_key] = (
            compute_percentile_rank(bucket_summary.get("atm_mean_iv"), bucket_history)
            if len(bucket_history) >= min_samples
            else None
        )

    enriched_term_structure = dict(term_structure)
    enriched_term_structure["short_term_iv_percentile"] = compute_percentile_rank(
        term_structure.get("short_term_atm_iv"),
        short_term_iv_history,
    )
    enriched_term_structure["longer_term_iv_percentile"] = compute_percentile_rank(
        term_structure.get("longer_term_atm_iv"),
        longer_term_iv_history,
    )
    volatility_summary["term_structure"] = enriched_term_structure
    volatility_summary["atm_iv_percentile"] = compute_percentile_rank(
        volatility_summary.get("atm_mean_iv"),
        atm_mean_iv_history,
    )
    volatility_summary["dte_bucket_iv_summary"] = dte_bucket_iv_summary
    volatility_summary["dte_bucket_iv_percentiles"] = bucket_percentiles
    volatility_summary["dte_bucket_iv_history_counts"] = bucket_history_counts
    if contracts:
        _annotate_contract_iv_percentiles(
            contracts,
            bucket_percentiles=bucket_percentiles,
            bucket_history_counts=bucket_history_counts,
            min_samples=min_samples,
        )

    symbol_history = _record_option_iv_history(
        underlying_symbol,
        atm_mean_iv=_safe_float(volatility_summary.get("atm_mean_iv")),
        short_term_atm_iv=_safe_float(term_structure.get("short_term_atm_iv")),
        longer_term_atm_iv=_safe_float(term_structure.get("longer_term_atm_iv")),
        bucket_atm_ivs={
            bucket_key: bucket_atm_iv
            for bucket_key, bucket_atm_iv in (
                (bucket_key, _safe_float(bucket_summary.get("atm_mean_iv")))
                for bucket_key, bucket_summary in dte_bucket_iv_summary.items()
            )
            if bucket_atm_iv is not None
        },
        recorded_at=recorded_at,
        path=path,
        max_entries=max_entries,
    )

    volatility_summary["iv_percentile_source"] = "alpaca_snapshot_history"
    volatility_summary["iv_history_path"] = str(path)
    volatility_summary["iv_history_sample_count"] = len(
        _historical_values_from_iv_entries(symbol_history.get("atm_mean_iv", []), max_entries=max_entries)
    )
    volatility_summary["iv_history_updated_at"] = str(symbol_history.get("updated_at") or "")
    return volatility_summary
