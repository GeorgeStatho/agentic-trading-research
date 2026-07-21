from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote as url_quote

try:
    import requests
except ImportError:  # pragma: no cover - dependency is installed in runtime images
    requests = None

from db_common import DB_PATH, coerce_float, get_connection, json_text

from .backtest_imports import _normalized_option_symbol, _normalized_timestamp
from .backtesting import _parse_timestamp
from .bootstrap import initialize_database


MARKETDATA_OPTION_QUOTES_URL = "https://api.marketdata.app/v1/options/quotes"
MARKETDATA_PROVIDER = "marketdata_app"
MARKETDATA_EOD_PRICING_SOURCE = "marketdata_eod"
DEFAULT_ENTRY_DELAY_MINUTES = 1_440
DEFAULT_MAX_HOLD_HOURS = 168


def _decision_rows(
    conn: sqlite3.Connection,
    *,
    start_at: str | None,
    end_at: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = ["LOWER(COALESCE(manager_decision, '')) IN ('call', 'put')", "TRIM(COALESCE(selected_option_symbol, '')) != ''"]
    params: list[Any] = []
    if start_at:
        clauses.append("decision_run_at >= ?")
        params.append(start_at)
    if end_at:
        clauses.append("decision_run_at <= ?")
        params.append(end_at)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT id, symbol, decision_run_at, selected_option_symbol
        FROM manager_decision_history
        WHERE {' AND '.join(clauses)}
        ORDER BY decision_run_at ASC, id ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _cached_quote_count(conn: sqlite3.Connection, option_symbol: str, start_at: str, end_at: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM historical_option_quotes
        WHERE option_symbol = ?
          AND quote_at >= ?
          AND quote_at <= ?
          AND bid_price > 0
          AND ask_price >= bid_price
        """,
        (option_symbol, start_at, end_at),
    ).fetchone()
    return int(row["count"] or 0) if row is not None else 0


def _store_quote_batch(conn: sqlite3.Connection, quotes: list[dict[str, Any]]) -> int:
    if not quotes:
        return 0
    params = [
        (
            quote["option_symbol"], quote["quote_at"], quote["bid_price"], quote["ask_price"],
            quote.get("last_price"), quote.get("volume"), quote.get("open_interest"),
            quote.get("implied_volatility"), quote.get("delta"), quote.get("gamma"), quote.get("theta"),
            quote.get("vega"), MARKETDATA_PROVIDER, json_text(quote),
        )
        for quote in quotes
    ]
    changes_before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO historical_option_quotes (
            option_symbol, quote_at, bid_price, ask_price, last_price, volume,
            open_interest, implied_volatility, delta, gamma, theta, vega, provider, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(option_symbol, quote_at, provider) DO NOTHING
        """,
        params,
    )
    return conn.total_changes - changes_before


def _record_coverage(
    conn: sqlite3.Connection,
    *,
    fetch_run_id: int,
    decision: dict[str, Any],
    option_symbol: str,
    requested_start_at: str,
    requested_end_at: str,
    status: str,
    quote_count: int = 0,
    page_count: int = 0,
    error_text: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO historical_option_quote_coverage (
            fetch_run_id, manager_decision_history_id, option_symbol, decision_at,
            requested_start_at, requested_end_at, status, quote_count, page_count,
            error_text, response_metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fetch_run_id, decision.get("id"), option_symbol, decision.get("decision_run_at"),
            requested_start_at, requested_end_at, status, quote_count, page_count,
            error_text, json_text(metadata or {}),
        ),
    )


def _marketdata_timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().replace(".", "", 1).isdigit()):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return _normalized_timestamp(value)


def _array_value(payload: dict[str, Any], field_name: str, index: int) -> Any:
    values = payload.get(field_name)
    return values[index] if isinstance(values, list) and index < len(values) else None


def _marketdata_quotes(option_symbol: str, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    updates = payload.get("updated")
    if not isinstance(updates, list):
        return [], 0
    quotes: list[dict[str, Any]] = []
    invalid_count = 0
    for index, updated in enumerate(updates):
        quote_at = _marketdata_timestamp(updated)
        bid_price = coerce_float(_array_value(payload, "bid", index))
        ask_price = coerce_float(_array_value(payload, "ask", index))
        if quote_at is None or bid_price is None or ask_price is None or bid_price <= 0 or ask_price <= 0 or ask_price < bid_price:
            invalid_count += 1
            continue
        returned_symbol = _normalized_option_symbol(_array_value(payload, "optionSymbol", index)) or option_symbol
        quotes.append(
            {
                "option_symbol": returned_symbol,
                "quote_at": quote_at,
                "bid_price": bid_price,
                "ask_price": ask_price,
                "last_price": coerce_float(_array_value(payload, "last", index)),
                "volume": coerce_float(_array_value(payload, "volume", index)),
                "open_interest": coerce_float(_array_value(payload, "openInterest", index)),
                "implied_volatility": coerce_float(_array_value(payload, "iv", index)),
                "delta": coerce_float(_array_value(payload, "delta", index)),
                "gamma": coerce_float(_array_value(payload, "gamma", index)),
                "theta": coerce_float(_array_value(payload, "theta", index)),
                "vega": coerce_float(_array_value(payload, "vega", index)),
                "pricing_source": MARKETDATA_EOD_PRICING_SOURCE,
                "raw_marketdata_quote": {
                    key: _array_value(payload, key, index)
                    for key in (
                        "optionSymbol", "ask", "askSize", "bid", "bidSize", "mid", "last", "volume",
                        "openInterest", "underlyingPrice", "updated", "iv", "delta", "gamma", "theta", "vega",
                    )
                },
            }
        )
    return quotes, invalid_count


def _request_marketdata_quotes(
    *,
    option_symbol: str,
    start_at: str,
    end_at: str,
    token: str,
    timeout_seconds: float,
    request_get: Callable[..., Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = request_get(
        f"{MARKETDATA_OPTION_QUOTES_URL}/{url_quote(option_symbol, safe='')}/",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"from": start_at[:10], "to": end_at[:10]},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("MarketData.app option quote response was not a JSON object.")
    headers = getattr(response, "headers", {}) or {}
    return payload, {
        "http_status": getattr(response, "status_code", None),
        "credits_consumed": headers.get("X-Api-Ratelimit-Consumed"),
        "credits_remaining": headers.get("X-Api-Ratelimit-Remaining"),
    }


def fetch_marketdata_eod_option_quotes(
    *,
    db_path: Path | str = DB_PATH,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int = 100,
    entry_delay_minutes: int = DEFAULT_ENTRY_DELAY_MINUTES,
    max_hold_hours: int = DEFAULT_MAX_HOLD_HOURS,
    timeout_seconds: float = 30.0,
    force: bool = False,
    token: str | None = None,
    request_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Fetch MarketData.app historical end-of-day option quotes for stored decisions.

    This operation reads manager history, writes quote/cache coverage only, and never
    calls a brokerage or order endpoint. Quotes are explicitly marked
    ``marketdata_eod`` so the backtest remains distinguishable from intraday data.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    if entry_delay_minutes < 1 or max_hold_hours < 1:
        raise ValueError("entry_delay_minutes and max_hold_hours must be at least 1.")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    normalized_start = _normalized_timestamp(start_at) if start_at else None
    normalized_end = _normalized_timestamp(end_at) if end_at else None
    if start_at and normalized_start is None:
        raise ValueError("start_at must be an ISO-8601 timestamp.")
    if end_at and normalized_end is None:
        raise ValueError("end_at must be an ISO-8601 timestamp.")
    if normalized_start and normalized_end and normalized_start > normalized_end:
        raise ValueError("start_at cannot be after end_at.")

    token_value = os.getenv("MARKETDATA_TOKEN") if token is None else token
    resolved_token = str(token_value or "").strip()
    resolved_request_get = request_get or (requests.get if requests is not None else None)
    initialize_database(db_path=db_path)
    config = {
        "start_at": normalized_start,
        "end_at": normalized_end,
        "limit": limit,
        "entry_delay_minutes": entry_delay_minutes,
        "max_hold_hours": max_hold_hours,
        "timeout_seconds": timeout_seconds,
        "force": force,
        "pricing_source": MARKETDATA_EOD_PRICING_SOURCE,
    }
    with get_connection(db_path) as conn:
        fetch_run_id = int(conn.execute(
            """
            INSERT INTO historical_option_quote_fetch_runs (provider, feed, config_json, started_at, status)
            VALUES (?, 'eod', ?, ?, 'running')
            """,
            (MARKETDATA_PROVIDER, json_text(config) or "{}", datetime.now(timezone.utc).isoformat()),
        ).lastrowid)
        decisions = _decision_rows(conn, start_at=normalized_start, end_at=normalized_end, limit=limit)

    result: dict[str, Any] = {
        "fetch_run_id": fetch_run_id,
        "provider": MARKETDATA_PROVIDER,
        "pricing_source": MARKETDATA_EOD_PRICING_SOURCE,
        "read_only_trading": True,
        "source_decision_count": len(decisions),
        "fetched_count": 0,
        "already_cached_count": 0,
        "no_data_count": 0,
        "unavailable_count": 0,
        "error_count": 0,
        "inserted_quote_count": 0,
        "invalid_quote_count": 0,
        "coverage_gaps": [],
    }
    credentials_error = "missing_marketdata_token" if not resolved_token else ""
    client_error = "requests_dependency_unavailable" if resolved_request_get is None else ""

    with get_connection(db_path) as conn:
        for decision in decisions:
            option_symbol = _normalized_option_symbol(decision.get("selected_option_symbol")) or ""
            decision_at = _parse_timestamp(decision.get("decision_run_at"))
            if not option_symbol or decision_at is None:
                result["unavailable_count"] += 1
                gap = {"decision_id": decision.get("id"), "option_symbol": option_symbol, "reason": "invalid_decision_contract_or_timestamp"}
                result["coverage_gaps"].append(gap)
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol or "unknown",
                    requested_start_at="", requested_end_at="", status="unavailable", error_text=gap["reason"],
                )
                continue
            requested_start = decision_at.isoformat()
            requested_end = (decision_at + timedelta(minutes=entry_delay_minutes, hours=max_hold_hours)).isoformat()
            cached_count = _cached_quote_count(conn, option_symbol, requested_start, requested_end)
            if cached_count >= 2 and not force:
                result["already_cached_count"] += 1
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                    requested_start_at=requested_start, requested_end_at=requested_end, status="already_cached",
                    quote_count=cached_count, metadata={"cached_quote_count": cached_count},
                )
                continue
            if credentials_error or client_error:
                reason = credentials_error or client_error
                result["unavailable_count"] += 1
                result["coverage_gaps"].append({"decision_id": decision["id"], "option_symbol": option_symbol, "reason": reason})
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                    requested_start_at=requested_start, requested_end_at=requested_end, status="unavailable", error_text=reason,
                )
                continue
            try:
                payload, metadata = _request_marketdata_quotes(
                    option_symbol=option_symbol, start_at=requested_start, end_at=requested_end,
                    token=resolved_token, timeout_seconds=timeout_seconds, request_get=resolved_request_get,
                )
                response_status = str(payload.get("s") or "").lower()
                if response_status == "error":
                    raise ValueError(str(payload.get("errmsg") or "MarketData.app returned an error response."))
                quotes, invalid_count = _marketdata_quotes(option_symbol, payload)
                result["invalid_quote_count"] += invalid_count
                inserted_count = _store_quote_batch(conn, quotes)
                result["inserted_quote_count"] += inserted_count
                if quotes:
                    result["fetched_count"] += 1
                    _record_coverage(
                        conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                        requested_start_at=requested_start, requested_end_at=requested_end, status="fetched",
                        quote_count=len(quotes), page_count=1,
                        metadata={**metadata, "inserted_quote_count": inserted_count, "invalid_quote_count": invalid_count, "pricing_source": MARKETDATA_EOD_PRICING_SOURCE},
                    )
                else:
                    result["no_data_count"] += 1
                    gap = {"decision_id": decision["id"], "option_symbol": option_symbol, "reason": "marketdata_no_valid_eod_quotes"}
                    result["coverage_gaps"].append(gap)
                    _record_coverage(
                        conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                        requested_start_at=requested_start, requested_end_at=requested_end, status="no_data",
                        error_text=gap["reason"], metadata={**metadata, "marketdata_status": response_status or None},
                    )
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                status = "unavailable" if status_code in {401, 403} else "no_data" if status_code == 404 else "error"
                result[f"{status}_count"] += 1
                reason = f"marketdata_request_failed:{type(exc).__name__}:{exc}"
                result["coverage_gaps"].append({"decision_id": decision["id"], "option_symbol": option_symbol, "reason": reason})
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                    requested_start_at=requested_start, requested_end_at=requested_end, status=status, error_text=reason,
                    metadata={"pricing_source": MARKETDATA_EOD_PRICING_SOURCE},
                )

        completed_at = datetime.now(timezone.utc).isoformat()
        final_status = "completed" if not (result["no_data_count"] or result["unavailable_count"] or result["error_count"]) else "completed_with_gaps"
        conn.execute(
            "UPDATE historical_option_quote_fetch_runs SET completed_at = ?, status = ?, summary_json = ? WHERE id = ?",
            (completed_at, final_status, json_text(result) or "{}", fetch_run_id),
        )
    result["status"] = final_status
    return result
