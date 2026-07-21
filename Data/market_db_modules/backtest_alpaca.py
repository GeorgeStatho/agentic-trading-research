from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import requests
except ImportError:  # pragma: no cover - dependency is installed in runtime images
    requests = None

from db_common import DB_PATH, coerce_float, get_connection, json_text

from .backtest_imports import _normalized_option_symbol, _normalized_timestamp
from .backtesting import _parse_timestamp
from .bootstrap import initialize_database


ALPACA_OPTION_QUOTES_URL = "https://data.alpaca.markets/v1beta1/options/quotes"
DEFAULT_ENTRY_DELAY_MINUTES = 1_440
DEFAULT_MAX_HOLD_HOURS = 168
ALPACA_HISTORICAL_OPTION_BID_ASK_UNAVAILABLE = "alpaca_historical_option_bid_ask_endpoint_unavailable"


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


def _store_quote_batch(conn: sqlite3.Connection, quotes: list[dict[str, Any]], provider: str) -> int:
    if not quotes:
        return 0
    params = [
        (
            quote["option_symbol"], quote["quote_at"], quote["bid_price"], quote["ask_price"],
            quote.get("last_price"), quote.get("volume"), quote.get("open_interest"),
            quote.get("implied_volatility"), quote.get("delta"), quote.get("gamma"), quote.get("theta"),
            quote.get("vega"), provider, json_text(quote),
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


def _api_quote(option_symbol: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    quote_at = _normalized_timestamp(payload.get("t") or payload.get("timestamp") or payload.get("quote_at"))
    bid_price = coerce_float(payload.get("bp") if "bp" in payload else payload.get("bid_price") or payload.get("bid"))
    ask_price = coerce_float(payload.get("ap") if "ap" in payload else payload.get("ask_price") or payload.get("ask"))
    if quote_at is None or bid_price is None or ask_price is None or bid_price <= 0 or ask_price <= 0 or ask_price < bid_price:
        return None
    return {
        "option_symbol": option_symbol,
        "quote_at": quote_at,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "raw_alpaca_quote": payload,
    }


def _quote_payload_rows(payload: dict[str, Any], option_symbol: str) -> list[dict[str, Any]]:
    raw_quotes = payload.get("quotes")
    if isinstance(raw_quotes, dict):
        rows = raw_quotes.get(option_symbol) or raw_quotes.get(option_symbol.upper()) or []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if isinstance(raw_quotes, list):
        return [row for row in raw_quotes if isinstance(row, dict)]
    return []


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


def _request_quote_pages(
    *,
    option_symbol: str,
    start_at: str,
    end_at: str,
    feed: str,
    page_limit: int,
    timeout_seconds: float,
    api_key: str,
    api_secret: str,
    request_get: Callable[..., Any],
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    page_token: str | None = None
    quote_rows: list[dict[str, Any]] = []
    page_count = 0
    while True:
        params: dict[str, Any] = {
            "symbols": option_symbol,
            "start": start_at,
            "end": end_at,
            "feed": feed,
            "limit": page_limit,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        response = request_get(
            ALPACA_OPTION_QUOTES_URL,
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            params=params,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Alpaca historical quote response was not a JSON object.")
        page_count += 1
        quote_rows.extend(_quote_payload_rows(payload, option_symbol))
        next_page_token = payload.get("next_page_token")
        if not next_page_token or next_page_token == page_token:
            return quote_rows, page_count, {"last_page_token": next_page_token or None}
        page_token = str(next_page_token)


def fetch_alpaca_historical_option_quotes(
    *,
    db_path: Path | str = DB_PATH,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int = 100,
    feed: str | None = None,
    entry_delay_minutes: int = DEFAULT_ENTRY_DELAY_MINUTES,
    max_hold_hours: int = DEFAULT_MAX_HOLD_HOURS,
    page_limit: int = 10_000,
    timeout_seconds: float = 30.0,
    force: bool = False,
    api_key: str | None = None,
    api_secret: str | None = None,
    request_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Fetch historical Alpaca option bid/ask quotes for stored manager decisions.

    This data-only operation never calls Alpaca trading endpoints or writes orders.
    Each decision receives a persisted coverage record, including no-data and
    unavailable-data gaps, so backtest fill rates remain explainable.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    if entry_delay_minutes < 1 or max_hold_hours < 1:
        raise ValueError("entry_delay_minutes and max_hold_hours must be at least 1.")
    if page_limit < 1 or page_limit > 10_000:
        raise ValueError("page_limit must be between 1 and 10000.")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    normalized_feed = str(feed or os.getenv("ALPACA_OPTION_HISTORICAL_FEED", "indicative")).strip().lower()
    if normalized_feed not in {"indicative", "opra"}:
        raise ValueError("feed must be 'indicative' or 'opra'.")
    normalized_start = _normalized_timestamp(start_at) if start_at else None
    normalized_end = _normalized_timestamp(end_at) if end_at else None
    if start_at and normalized_start is None:
        raise ValueError("start_at must be an ISO-8601 timestamp.")
    if end_at and normalized_end is None:
        raise ValueError("end_at must be an ISO-8601 timestamp.")
    if normalized_start and normalized_end and normalized_start > normalized_end:
        raise ValueError("start_at cannot be after end_at.")

    resolved_api_key = str(api_key or os.getenv("PUBLIC_KEY") or "").strip()
    resolved_api_secret = str(api_secret or os.getenv("PRIVATE_KEY") or "").strip()
    resolved_request_get = request_get or (requests.get if requests is not None else None)
    initialize_database(db_path=db_path)
    config = {
        "start_at": normalized_start,
        "end_at": normalized_end,
        "limit": limit,
        "entry_delay_minutes": entry_delay_minutes,
        "max_hold_hours": max_hold_hours,
        "page_limit": page_limit,
        "timeout_seconds": timeout_seconds,
        "force": force,
    }
    started_at = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        fetch_run_id = int(conn.execute(
            """
            INSERT INTO historical_option_quote_fetch_runs (provider, feed, config_json, started_at, status)
            VALUES ('alpaca', ?, ?, ?, 'running')
            """,
            (normalized_feed, json_text(config) or "{}", started_at),
        ).lastrowid)
        decisions = _decision_rows(conn, start_at=normalized_start, end_at=normalized_end, limit=limit)

    result: dict[str, Any] = {
        "fetch_run_id": fetch_run_id,
        "provider": "alpaca",
        "feed": normalized_feed,
        "read_only_trading": True,
        "source_decision_count": len(decisions),
        "fetched_count": 0,
        "already_cached_count": 0,
        "no_data_count": 0,
        "unavailable_count": 0,
        "error_count": 0,
        "inserted_quote_count": 0,
        "invalid_quote_count": 0,
        "historical_bid_ask_supported": None,
        "coverage_gaps": [],
    }
    credentials_error = "missing_alpaca_credentials" if not resolved_api_key or not resolved_api_secret else ""
    client_error = "requests_dependency_unavailable" if resolved_request_get is None else ""
    endpoint_unavailable_reason = ""

    with get_connection(db_path) as conn:
        for decision in decisions:
            option_symbol = _normalized_option_symbol(decision.get("selected_option_symbol")) or ""
            decision_at = _parse_timestamp(decision.get("decision_run_at"))
            if not option_symbol or decision_at is None:
                result["unavailable_count"] += 1
                result["coverage_gaps"].append({"decision_id": decision.get("id"), "option_symbol": option_symbol, "reason": "invalid_decision_contract_or_timestamp"})
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol or "unknown",
                    requested_start_at="", requested_end_at="", status="unavailable",
                    error_text="invalid_decision_contract_or_timestamp",
                )
                continue
            requested_start = decision_at.isoformat()
            requested_end = (
                decision_at + timedelta(minutes=entry_delay_minutes, hours=max_hold_hours)
            ).isoformat()
            cached_count = _cached_quote_count(conn, option_symbol, requested_start, requested_end)
            if cached_count >= 2 and not force:
                result["already_cached_count"] += 1
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                    requested_start_at=requested_start, requested_end_at=requested_end, status="already_cached",
                    quote_count=cached_count, metadata={"cached_quote_count": cached_count},
                )
                continue
            if endpoint_unavailable_reason:
                result["unavailable_count"] += 1
                result["coverage_gaps"].append({"decision_id": decision["id"], "option_symbol": option_symbol, "reason": endpoint_unavailable_reason})
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                    requested_start_at=requested_start, requested_end_at=requested_end, status="unavailable",
                    error_text=endpoint_unavailable_reason,
                    metadata={"provider_capability": "historical_option_bid_ask_unavailable"},
                )
                continue
            if credentials_error or client_error:
                reason = credentials_error or client_error
                result["unavailable_count"] += 1
                result["coverage_gaps"].append({"decision_id": decision["id"], "option_symbol": option_symbol, "reason": reason})
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                    requested_start_at=requested_start, requested_end_at=requested_end, status="unavailable",
                    error_text=reason,
                )
                continue
            try:
                raw_rows, page_count, metadata = _request_quote_pages(
                    option_symbol=option_symbol, start_at=requested_start, end_at=requested_end,
                    feed=normalized_feed, page_limit=page_limit, timeout_seconds=timeout_seconds,
                    api_key=resolved_api_key, api_secret=resolved_api_secret, request_get=resolved_request_get,
                )
                quotes = [quote for row in raw_rows if (quote := _api_quote(option_symbol, row)) is not None]
                invalid_count = len(raw_rows) - len(quotes)
                result["invalid_quote_count"] += invalid_count
                inserted_count = _store_quote_batch(conn, quotes, "alpaca")
                result["inserted_quote_count"] += inserted_count
                if quotes:
                    result["historical_bid_ask_supported"] = True
                    result["fetched_count"] += 1
                    _record_coverage(
                        conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                        requested_start_at=requested_start, requested_end_at=requested_end, status="fetched",
                        quote_count=len(quotes), page_count=page_count,
                        metadata={**metadata, "inserted_quote_count": inserted_count, "invalid_quote_count": invalid_count},
                    )
                else:
                    result["no_data_count"] += 1
                    gap = {"decision_id": decision["id"], "option_symbol": option_symbol, "reason": "no_valid_quotes_returned"}
                    result["coverage_gaps"].append(gap)
                    _record_coverage(
                        conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                        requested_start_at=requested_start, requested_end_at=requested_end, status="no_data",
                        quote_count=0, page_count=page_count, error_text=gap["reason"], metadata=metadata,
                    )
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                status = "unavailable" if status_code in {401, 403, 404, 422} else "error"
                result[f"{status}_count"] += 1
                if status_code == 404:
                    endpoint_unavailable_reason = ALPACA_HISTORICAL_OPTION_BID_ASK_UNAVAILABLE
                    result["historical_bid_ask_supported"] = False
                    reason = endpoint_unavailable_reason
                else:
                    reason = f"alpaca_request_failed:{type(exc).__name__}:{exc}"
                result["coverage_gaps"].append({"decision_id": decision["id"], "option_symbol": option_symbol, "reason": reason})
                _record_coverage(
                    conn, fetch_run_id=fetch_run_id, decision=decision, option_symbol=option_symbol,
                    requested_start_at=requested_start, requested_end_at=requested_end, status=status,
                    error_text=reason,
                )

        completed_at = datetime.now(timezone.utc).isoformat()
        final_status = "completed" if not (result["no_data_count"] or result["unavailable_count"] or result["error_count"]) else "completed_with_gaps"
        conn.execute(
            """
            UPDATE historical_option_quote_fetch_runs
            SET completed_at = ?, status = ?, summary_json = ?
            WHERE id = ?
            """,
            (completed_at, final_status, json_text(result) or "{}", fetch_run_id),
        )
    result["status"] = final_status
    return result
