from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable
from zoneinfo import ZoneInfo

from db_common import DB_PATH, clean_text, coerce_float, get_connection, json_text, table_exists
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp
from services.option_pricing import CalibratedOption, expiration_close, option_side


SNAPSHOT_VERSION = "decision-evidence-snapshot-v1"
BACKTEST_ENGINE_VERSION = "portfolio-guardrails-bid-ask-v2"
MODELLED_PRICING_SOURCE = "model_black_scholes_constant_iv"
MODELLED_QUOTE_PROVIDER = "frozen_decision_chain"
MISSING_QUOTE_WARNINGS = {"no_valid_historical_entry_quote", "next_quote_exceeds_entry_delay"}
MARKET_TIMEZONE = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16)
SECONDS_PER_DAY = 86400
DAYS_PER_YEAR = 365
DEFAULT_BACKTEST_CONFIG: dict[str, Any] = {
    "strategy_version": "frozen-manager-decision-v1",
    "model": "stored-manager-decision",
    "prompt_version": "frozen-input-v1",
    "entry_slippage_bps": 25.0,
    "exit_slippage_bps": 25.0,
    "commission_per_contract": 0.65,
    "option_multiplier": 100,
    "default_order_qty": 1,
    "max_spread_pct": 0.25,
    "min_volume": 0.0,
    "max_entry_delay_minutes": 1440,
    "max_hold_hours": 168,
    "take_profit_pct": 50.0,
    "stop_loss_pct": -50.0,
    "max_concurrent_positions": 5,
    "max_total_entry_notional": 50000.0,
    "max_sector_notional_pct": 50.0,
    "daily_loss_limit_dollars": 2000.0,
    "risk_free_rate": 0.04,
    "data_source_versions": {"option_quotes": "historical_option_quotes"},
}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _number(value: Any, fallback: float = 0.0) -> float:
    parsed = coerce_float(value)
    return parsed if parsed is not None else fallback


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _stable_json_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _article_references(manager_input: dict[str, Any]) -> list[dict[str, Any]]:
    references = manager_input.get("article_references")
    if not isinstance(references, list):
        supporting = manager_input.get("supporting_articles")
        references = supporting.get("article_summaries") if isinstance(supporting, dict) else []
    return [item for item in references if isinstance(item, dict)] if isinstance(references, list) else []


def _available_articles(conn: sqlite3.Connection, references: list[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    if not references:
        return []
    article_ids = [_positive_int(item.get("article_id") or item.get("id")) for item in references]
    article_ids = [item for item in article_ids if item > 0]
    persisted: dict[int, dict[str, Any]] = {}
    if article_ids and table_exists(conn, "news_articles"):
        placeholders = ",".join("?" for _ in article_ids)
        rows = conn.execute(
            f"""
            SELECT id, title, summary, content_hash, published_at, created_at, evidence_score
            FROM news_articles
            WHERE id IN ({placeholders})
            """,
            tuple(article_ids),
        ).fetchall()
        persisted = {int(row["id"]): dict(row) for row in rows}
    output: list[dict[str, Any]] = []
    for reference in references:
        article_id = _positive_int(reference.get("article_id") or reference.get("id"))
        persisted_article = persisted.get(article_id)
        if persisted_article is not None:
            published_at = _parse_timestamp(persisted_article.get("published_at"))
            ingested_at = _parse_timestamp(persisted_article.get("created_at"))
            if (published_at is not None and published_at > as_of) or (ingested_at is not None and ingested_at > as_of):
                continue
            output.append(
                {
                    "id": article_id,
                    "published_at": str(persisted_article.get("published_at") or ""),
                    "ingested_at": str(persisted_article.get("created_at") or ""),
                    "content_hash": str(persisted_article.get("content_hash") or ""),
                    "evidence_score": coerce_float(persisted_article.get("evidence_score")),
                    "title": str(persisted_article.get("title") or ""),
                    "summary": str(persisted_article.get("summary") or reference.get("summary") or ""),
                    "entity_links": [str(reference.get("article_scope") or "").strip()],
                    "availability": "persisted_ingestion_time",
                }
            )
            continue
        output.append(
            {
                "id": article_id,
                "published_at": str(reference.get("published_at") or ""),
                "ingested_at": str(reference.get("ingested_at") or ""),
                "content_hash": str(reference.get("content_hash") or ""),
                "evidence_score": coerce_float(reference.get("evidence_score")),
                "title": str(reference.get("title") or ""),
                "summary": str(reference.get("summary") or ""),
                "entity_links": [str(reference.get("article_scope") or "").strip()],
                "availability": "frozen_decision_input",
            }
        )
    return output


def ensure_decision_evidence_snapshot(
    decision: dict[str, Any],
    *,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    decision_id = coerce_int(decision.get("id"))
    if decision_id is None:
        raise ValueError("Backtest decision is missing an ID.")
    existing = conn.execute(
        "SELECT * FROM decision_evidence_snapshots WHERE manager_decision_history_id = ?",
        (decision_id,),
    ).fetchone()
    if existing is not None:
        return dict(existing)
    as_of = _parse_timestamp(decision.get("decision_run_at"))
    if as_of is None:
        raise ValueError("Backtest decision is missing a valid decision timestamp.")
    manager_input = _json_object(decision.get("manager_input_json"))
    manager_output = _json_object(decision.get("manager_output_json"))
    articles = _available_articles(conn, _article_references(manager_input), as_of)
    snapshot = {
        "as_of": as_of.isoformat(),
        "article_ids": [article["id"] for article in articles if _positive_int(article.get("id")) > 0],
        "articles": articles,
        "market_context": manager_input.get("market_context") if isinstance(manager_input.get("market_context"), dict) else {},
        "research_payload": manager_input,
        "manager_output": manager_output,
        "availability_rule": "published_at_and_ingested_at_must_be_at_or_before_as_of",
    }
    snapshot_hash = _stable_json_hash(snapshot)
    cursor = conn.execute(
        """
        INSERT INTO decision_evidence_snapshots (
            manager_decision_history_id, as_of, snapshot_version, snapshot_hash,
            article_ids_json, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            as_of.isoformat(),
            SNAPSHOT_VERSION,
            snapshot_hash,
            json_text(snapshot["article_ids"]) or "[]",
            json_text({**snapshot, "snapshot_hash": snapshot_hash}) or "{}",
        ),
    )
    return {
        "id": int(cursor.lastrowid),
        "manager_decision_history_id": decision_id,
        "as_of": as_of.isoformat(),
        "snapshot_version": SNAPSHOT_VERSION,
        "snapshot_hash": snapshot_hash,
        "article_ids_json": json_text(snapshot["article_ids"]) or "[]",
        "snapshot_json": json_text({**snapshot, "snapshot_hash": snapshot_hash}) or "{}",
    }


def record_historical_option_quote(
    quote: dict[str, Any],
    *,
    provider: str,
    db_path: Path | str = DB_PATH,
) -> int | None:
    option_symbol = clean_text(quote.get("option_symbol") or quote.get("symbol"))
    quote_at = clean_text(quote.get("quote_at") or quote.get("timestamp") or quote.get("captured_at"))
    normalized_provider = clean_text(provider)
    if option_symbol is None or quote_at is None or normalized_provider is None:
        return None
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO historical_option_quotes (
                option_symbol, quote_at, bid_price, ask_price, last_price, volume,
                open_interest, implied_volatility, delta, gamma, theta, vega, provider, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(option_symbol, quote_at, provider) DO NOTHING
            RETURNING id
            """,
            (
                option_symbol.upper(), normalize_timestamp(quote_at), coerce_float(quote.get("bid_price") or quote.get("bid")),
                coerce_float(quote.get("ask_price") or quote.get("ask")), coerce_float(quote.get("last_price") or quote.get("last")),
                coerce_float(quote.get("volume")), coerce_float(quote.get("open_interest")),
                coerce_float(quote.get("implied_volatility") or quote.get("iv")), coerce_float(quote.get("delta")),
                coerce_float(quote.get("gamma")), coerce_float(quote.get("theta")), coerce_float(quote.get("vega")),
                normalized_provider, json_text(quote),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM historical_option_quotes WHERE option_symbol = ? AND quote_at = ? AND provider = ?",
                (option_symbol.upper(), normalize_timestamp(quote_at), normalized_provider),
            ).fetchone()
        return int(row["id"]) if row is not None else None


def record_historical_underlying_bar(
    bar: dict[str, Any],
    *,
    provider: str,
    timeframe: str = "1d",
    db_path: Path | str = DB_PATH,
) -> int | None:
    symbol = clean_text(bar.get("symbol"))
    bar_at = clean_text(bar.get("bar_at") or bar.get("timestamp") or bar.get("captured_at"))
    normalized_provider = clean_text(provider)
    if symbol is None or bar_at is None or normalized_provider is None:
        return None
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO historical_underlying_bars (
                symbol, bar_at, timeframe, open_price, high_price, low_price, close_price,
                volume, provider, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, bar_at, timeframe, provider) DO NOTHING
            RETURNING id
            """,
            (
                symbol.upper(), normalize_timestamp(bar_at), str(timeframe or "1d"),
                coerce_float(bar.get("open_price") or bar.get("open")), coerce_float(bar.get("high_price") or bar.get("high")),
                coerce_float(bar.get("low_price") or bar.get("low")), coerce_float(bar.get("close_price") or bar.get("close")),
                coerce_float(bar.get("volume")), normalized_provider, json_text(bar),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM historical_underlying_bars WHERE symbol = ? AND bar_at = ? AND timeframe = ? AND provider = ?",
                (symbol.upper(), normalize_timestamp(bar_at), str(timeframe or "1d"), normalized_provider),
            ).fetchone()
        return int(row["id"]) if row is not None else None


def record_historical_option_contract(
    contract: dict[str, Any],
    *,
    provider: str,
    db_path: Path | str = DB_PATH,
) -> int | None:
    option_symbol = clean_text(contract.get("option_symbol") or contract.get("symbol"))
    underlying_symbol = clean_text(contract.get("underlying_symbol"))
    captured_at = clean_text(contract.get("captured_at") or contract.get("timestamp"))
    normalized_provider = clean_text(provider)
    if option_symbol is None or underlying_symbol is None or captured_at is None or normalized_provider is None:
        return None
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO historical_option_contracts (
                option_symbol, underlying_symbol, contract_type, expiration_date, strike_price,
                provider, metadata_json, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(option_symbol, provider, captured_at) DO NOTHING
            RETURNING id
            """,
            (
                option_symbol.upper(), underlying_symbol.upper(), clean_text(contract.get("contract_type") or contract.get("type")),
                clean_text(contract.get("expiration_date") or contract.get("expiration")), coerce_float(contract.get("strike_price") or contract.get("strike")),
                normalized_provider, json_text(contract), normalize_timestamp(captured_at),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM historical_option_contracts WHERE option_symbol = ? AND provider = ? AND captured_at = ?",
                (option_symbol.upper(), normalized_provider, normalize_timestamp(captured_at)),
            ).fetchone()
        return int(row["id"]) if row is not None else None


def _merged_config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_BACKTEST_CONFIG}
    if isinstance(config, dict):
        for key in DEFAULT_BACKTEST_CONFIG:
            if key in config:
                merged[key] = config[key]
    for key in ("entry_slippage_bps", "exit_slippage_bps", "commission_per_contract", "max_spread_pct", "min_volume", "take_profit_pct", "stop_loss_pct", "max_total_entry_notional", "max_sector_notional_pct", "daily_loss_limit_dollars", "risk_free_rate"):
        merged[key] = _number(merged[key])
    for key in ("option_multiplier", "default_order_qty", "max_entry_delay_minutes", "max_hold_hours", "max_concurrent_positions"):
        merged[key] = max(1, int(_number(merged[key], 1)))
    merged["max_total_entry_notional"] = max(1.0, merged["max_total_entry_notional"])
    merged["max_sector_notional_pct"] = min(100.0, max(0.01, merged["max_sector_notional_pct"]))
    merged["daily_loss_limit_dollars"] = max(1.0, merged["daily_loss_limit_dollars"])
    merged["strategy_version"] = str(merged["strategy_version"] or DEFAULT_BACKTEST_CONFIG["strategy_version"])
    merged["model"] = str(merged["model"] or "")
    merged["prompt_version"] = str(merged["prompt_version"] or "")
    merged["data_source_versions"] = merged["data_source_versions"] if isinstance(merged["data_source_versions"], dict) else {}
    return merged


def _valid_quote(quote: dict[str, Any], config: dict[str, Any]) -> bool:
    bid = _number(quote.get("bid_price"))
    ask = _number(quote.get("ask_price"))
    if bid <= 0 or ask <= 0 or ask < bid:
        return False
    if (ask - bid) / ask > config["max_spread_pct"]:
        return False
    volume = quote.get("volume")
    return volume is None or _number(volume) >= config["min_volume"]


def _quote_path(conn: sqlite3.Connection, option_symbol: str, as_of: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM historical_option_quotes
        WHERE option_symbol = ? AND quote_at >= ?
        ORDER BY quote_at ASC, id ASC
        """,
        (option_symbol, as_of.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _pricing_source(quote: dict[str, Any]) -> str:
    raw_quote = _json_object(quote.get("raw_json"))
    return str(raw_quote.get("pricing_source") or "observed_quote")


def _base_event(decision: dict[str, Any], snapshot: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    as_of = _parse_timestamp(decision.get("decision_run_at"))
    return {
        "as_of": as_of.isoformat() if as_of is not None else "",
        "manager_decision_history_id": int(decision["id"]),
        "evidence_snapshot_id": int(snapshot["id"]),
        "symbol": str(decision.get("symbol") or "").strip().upper(),
        "decision": str(decision.get("manager_decision") or "").strip().lower(),
        "option_symbol": str(decision.get("selected_option_symbol") or "").strip().upper(),
        "rationale": str(decision.get("manager_reason") or "").strip(),
        "portfolio_sector": str(decision.get("sector_key") or "unknown").strip().lower() or "unknown",
        "quantity": max(1, coerce_int(decision.get("order_qty")) or config["default_order_qty"]),
        "warnings": [],
    }


def _first_exit(
    marks: list[tuple[datetime, float]],
    *,
    entry_price: float,
    quantity: int,
    config: dict[str, Any],
) -> tuple[tuple[datetime, float] | None, list[str]]:
    """Walk (time, bid) marks in order: exit at take-profit, stop-loss, or the last mark."""
    multiplier = config["option_multiplier"]
    commission = config["commission_per_contract"] * quantity * 2
    entry_notional = entry_price * multiplier * quantity
    exit_mark: tuple[datetime, float] | None = None
    for mark_at, bid in marks:
        exit_price = bid * (1 - config["exit_slippage_bps"] / 10_000)
        pnl_pct = ((exit_price * multiplier * quantity - entry_notional - commission) / entry_notional) * 100
        exit_mark = (mark_at, bid)
        if pnl_pct >= config["take_profit_pct"]:
            return exit_mark, ["take_profit_exit"]
        if pnl_pct <= config["stop_loss_pct"]:
            return exit_mark, ["stop_loss_exit"]
    return exit_mark, []


def _priced_event(
    base_event: dict[str, Any],
    *,
    entry_at: datetime,
    entry_ask: float,
    marks: list[tuple[datetime, float]],
    risk_release_at: datetime,
    quote_provider: Any,
    pricing_source: str,
    missing_exit_warning: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    quantity = base_event["quantity"]
    multiplier = config["option_multiplier"]
    entry_price = entry_ask * (1 + config["entry_slippage_bps"] / 10_000)
    entry_notional = entry_price * multiplier * quantity
    commission = config["commission_per_contract"] * quantity * 2
    exit_mark, warnings = _first_exit(marks, entry_price=entry_price, quantity=quantity, config=config)
    if exit_mark is None:
        return {
            **base_event,
            "status": "no_exit_quote",
            "entry_at": entry_at.isoformat(),
            "entry_price": entry_price,
            "entry_notional": round(entry_notional, 4),
            "risk_release_at": risk_release_at.isoformat(),
            "quote_provider": quote_provider,
            "pricing_source": pricing_source,
            "warnings": [missing_exit_warning],
        }
    exit_at, exit_bid = exit_mark
    exit_price = exit_bid * (1 - config["exit_slippage_bps"] / 10_000)
    realized_pnl_dollars = (exit_price - entry_price) * multiplier * quantity - commission
    realized_pnl_pct = (realized_pnl_dollars / entry_notional) * 100 if entry_notional else None
    return {
        **base_event,
        "status": "simulated_closed",
        "entry_at": entry_at.isoformat(),
        "exit_at": exit_at.isoformat(),
        "entry_price": round(entry_price, 6),
        "exit_price": round(exit_price, 6),
        "entry_notional": round(entry_notional, 4),
        "risk_release_at": exit_at.isoformat(),
        "quote_provider": quote_provider,
        "pricing_source": pricing_source,
        "realized_pnl_pct": round(realized_pnl_pct, 4) if realized_pnl_pct is not None else None,
        "realized_pnl_dollars": round(realized_pnl_dollars, 4),
        "commission_dollars": round(commission, 4),
        "slippage_dollars": round(
            ((entry_ask * config["entry_slippage_bps"] / 10_000)
             + (exit_bid * config["exit_slippage_bps"] / 10_000)) * multiplier * quantity,
            4,
        ),
        "warnings": warnings,
    }


def _simulate_event(decision: dict[str, Any], snapshot: dict[str, Any], conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    as_of = _parse_timestamp(decision.get("decision_run_at"))
    base_event = _base_event(decision, snapshot, config)
    option_symbol = base_event["option_symbol"]
    if as_of is None or not option_symbol:
        return {**base_event, "status": "no_fill", "warnings": ["missing_decision_timestamp_or_contract"]}
    quotes = [quote for quote in _quote_path(conn, option_symbol, as_of) if _valid_quote(quote, config)]
    if not quotes:
        return {**base_event, "status": "no_fill", "warnings": ["no_valid_historical_entry_quote"]}
    entry_quote = quotes[0]
    entry_at = _parse_timestamp(entry_quote.get("quote_at"))
    if entry_at is None or (entry_at - as_of).total_seconds() > config["max_entry_delay_minutes"] * 60:
        return {**base_event, "status": "no_fill", "warnings": ["next_quote_exceeds_entry_delay"]}
    cutoff = entry_at + timedelta(hours=config["max_hold_hours"])
    marks: list[tuple[datetime, float]] = []
    for quote in quotes[1:]:
        quote_at = _parse_timestamp(quote.get("quote_at"))
        if quote_at is None or quote_at > cutoff:
            break
        marks.append((quote_at, _number(quote.get("bid_price"))))
    return _priced_event(
        base_event,
        entry_at=entry_at,
        entry_ask=_number(entry_quote.get("ask_price")),
        marks=marks,
        risk_release_at=cutoff,
        quote_provider=entry_quote.get("provider"),
        pricing_source=_pricing_source(entry_quote),
        missing_exit_warning="no_valid_historical_exit_quote",
        config=config,
    )


def _daily_closes(conn: sqlite3.Connection, symbol: str, after: datetime, until: datetime) -> list[tuple[datetime, float]]:
    """Underlying closes stamped at 16:00 New York on each bar's trading day, one per day."""
    rows = conn.execute(
        """
        SELECT bar_at, close_price FROM historical_underlying_bars
        WHERE symbol = ? AND timeframe = '1d' AND close_price > 0 AND bar_at >= ? AND bar_at <= ?
        ORDER BY bar_at ASC, id ASC
        """,
        # Stored bar times are UTC text, so both bounds must be UTC to compare as strings.
        (symbol, (after - timedelta(days=2)).astimezone(timezone.utc).isoformat(),
         until.astimezone(timezone.utc).isoformat()),
    ).fetchall()
    closes: dict[datetime, float] = {}
    for row in rows:
        bar_at = _parse_timestamp(row["bar_at"])
        if bar_at is None:
            continue
        close_at = datetime.combine(bar_at.astimezone(MARKET_TIMEZONE).date(), MARKET_CLOSE, MARKET_TIMEZONE)
        if after < close_at <= until:
            closes.setdefault(close_at, float(row["close_price"]))
    return sorted(closes.items())


def _simulate_modelled_event(
    decision: dict[str, Any],
    snapshot: dict[str, Any],
    conn: sqlite3.Connection,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Enter at the frozen chain's ask; mark exits with Black-Scholes on daily underlying closes.

    The contract keeps the implied volatility of its entry mid, so a post-event volatility
    collapse is not modelled. Exits give up the entry quote's half-spread on the model mid.
    """
    base_event = _base_event(decision, snapshot, config)
    as_of = _parse_timestamp(decision.get("decision_run_at"))
    contract = decision.get("selected_contract") or {}
    quote = contract.get("latest_quote") or {}
    side = option_side(contract.get("contract_type"))
    strike = _number(contract.get("strike_price"))
    spot = _number(decision.get("reference_spot"))
    expiration = expiration_close(contract.get("expiration_date"))
    if (as_of is None or expiration is None or expiration <= as_of or side not in {"call", "put"}
            or strike <= 0 or spot <= 0 or not _valid_quote(quote, config)):
        return {**base_event, "status": "no_fill", "warnings": ["no_valid_frozen_entry_quote"]}
    bid, ask = _number(quote.get("bid_price")), _number(quote.get("ask_price"))
    mid = (bid + ask) / 2
    years = (expiration - as_of).total_seconds() / SECONDS_PER_DAY / DAYS_PER_YEAR
    option = CalibratedOption.from_mid(side, mid, spot=spot, strike=strike, years=years, rate=config["risk_free_rate"])
    if option is None:
        return {**base_event, "status": "no_fill", "warnings": ["implied_volatility_unavailable"]}
    half_spread = (ask - bid) / 2 / mid
    cutoff = min(as_of + timedelta(hours=config["max_hold_hours"]), expiration)
    marks = [
        (close_at, option.value_after((close_at - as_of).total_seconds() / SECONDS_PER_DAY, move=close / spot - 1)
         * (1 - half_spread))
        for close_at, close in _daily_closes(conn, base_event["symbol"], as_of, cutoff)
    ]
    return _priced_event(
        base_event,
        entry_at=as_of,
        entry_ask=ask,
        marks=marks,
        risk_release_at=cutoff,
        quote_provider=MODELLED_QUOTE_PROVIDER,
        pricing_source=MODELLED_PRICING_SOURCE,
        missing_exit_warning="no_underlying_close_after_entry",
        config=config,
    )


def _simulate_decision(decision: dict[str, Any], snapshot: dict[str, Any], conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    """Observed quotes first; a decision carrying its frozen contract falls back to model pricing."""
    event = _simulate_event(decision, snapshot, conn, config)
    if decision.get("selected_contract") and MISSING_QUOTE_WARNINGS.intersection(event["warnings"]):
        return _simulate_modelled_event(decision, snapshot, conn, config)
    return event


def _apply_portfolio_limits(events: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject candidate entries that exceed deterministic portfolio guardrails."""
    active_positions: list[dict[str, Any]] = []
    realized_losses_by_day: dict[str, float] = {}

    candidates = [
        event for event in events
        if event.get("status") in {"simulated_closed", "no_exit_quote"}
        and _parse_timestamp(event.get("entry_at")) is not None
    ]
    for event in sorted(candidates, key=lambda item: (str(item.get("entry_at") or ""), int(item.get("manager_decision_history_id") or 0))):
        entry_at = _parse_timestamp(event.get("entry_at"))
        if entry_at is None:
            continue
        remaining_positions: list[dict[str, Any]] = []
        for position in active_positions:
            release_at = _parse_timestamp(position.get("risk_release_at"))
            if release_at is None or release_at > entry_at:
                remaining_positions.append(position)
                continue
            realized_pnl = _number(position.get("realized_pnl_dollars"))
            if realized_pnl < 0:
                loss_day = release_at.date().isoformat()
                realized_losses_by_day[loss_day] = realized_losses_by_day.get(loss_day, 0.0) + abs(realized_pnl)
        active_positions = remaining_positions

        entry_notional = _number(event.get("entry_notional"))
        sector_key = str(event.get("portfolio_sector") or "unknown").strip().lower() or "unknown"
        active_notional = sum(_number(position.get("entry_notional")) for position in active_positions)
        sector_notional = sum(
            _number(position.get("entry_notional"))
            for position in active_positions
            if str(position.get("portfolio_sector") or "unknown").strip().lower() == sector_key
        )
        sector_limit = config["max_total_entry_notional"] * (config["max_sector_notional_pct"] / 100.0)
        daily_losses = realized_losses_by_day.get(entry_at.date().isoformat(), 0.0)
        rejection_reason = ""
        if daily_losses >= config["daily_loss_limit_dollars"]:
            rejection_reason = "portfolio_daily_loss_limit"
        elif len(active_positions) >= config["max_concurrent_positions"]:
            rejection_reason = "portfolio_max_concurrent_positions"
        elif active_notional + entry_notional > config["max_total_entry_notional"]:
            rejection_reason = "portfolio_total_entry_notional_limit"
        elif sector_notional + entry_notional > sector_limit:
            rejection_reason = "portfolio_sector_notional_limit"

        if rejection_reason:
            event.update(
                {
                    "status": "portfolio_rejected",
                    "entry_at": None,
                    "exit_at": None,
                    "entry_price": None,
                    "exit_price": None,
                    "realized_pnl_pct": None,
                    "realized_pnl_dollars": None,
                    "commission_dollars": None,
                    "slippage_dollars": None,
                    "warnings": [rejection_reason],
                }
            )
            continue
        active_positions.append(event)
    return events


def _metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [event for event in events if event.get("status") == "simulated_closed"]
    no_fill = [event for event in events if event.get("status") in {"no_fill", "portfolio_rejected"}]
    pnl_values = [float(event["realized_pnl_pct"]) for event in filled if event.get("realized_pnl_pct") is not None]
    pnl_dollars = [float(event["realized_pnl_dollars"]) for event in filled if event.get("realized_pnl_dollars") is not None]
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    gross_loss = abs(sum(losses))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    equity_curve = []
    for event in sorted(filled, key=lambda item: (str(item.get("exit_at") or ""), int(item.get("manager_decision_history_id") or 0))):
        cumulative += _number(event.get("realized_pnl_dollars"))
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        equity_curve.append({"at": event.get("exit_at"), "pnl_dollars": round(cumulative, 4)})
    return {
        "event_count": len(events),
        "filled_count": len(filled),
        "no_fill_count": len(no_fill),
        "no_exit_quote_count": len([event for event in events if event.get("status") == "no_exit_quote"]),
        "portfolio_rejected_count": len([event for event in events if event.get("status") == "portfolio_rejected"]),
        "fill_rate_pct": round((len(filled) / len(events)) * 100, 2) if events else None,
        "win_rate_pct": round((len(wins) / len(pnl_values)) * 100, 2) if pnl_values else None,
        "expectancy_pct": round(sum(pnl_values) / len(pnl_values), 4) if pnl_values else None,
        "median_pnl_pct": round(float(median(pnl_values)), 4) if pnl_values else None,
        "profit_factor_like": round(sum(wins) / gross_loss, 4) if gross_loss else None,
        "total_pnl_dollars": round(sum(pnl_dollars), 4),
        "total_commission_dollars": round(sum(_number(event.get("commission_dollars")) for event in filled), 4),
        "total_slippage_dollars": round(sum(_number(event.get("slippage_dollars")) for event in filled), 4),
        "max_drawdown_dollars": round(max_drawdown, 4),
        "pricing_source_counts": _pricing_source_counts(filled),
        "equity_curve": equity_curve,
    }


def _pricing_source_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        source = str(event.get("pricing_source") or "unknown")
        counts[source] = counts.get(source, 0) + 1
    return counts


def _store_event(conn: sqlite3.Connection, run_id: int, event: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO backtest_events (
            run_id, as_of, manager_decision_history_id, evidence_snapshot_id, symbol,
            decision, option_symbol, status, rationale, entry_at, exit_at, entry_price,
            exit_price, quantity, realized_pnl_pct, realized_pnl_dollars, commission_dollars,
            slippage_dollars, warnings_json, event_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, event.get("as_of"), event.get("manager_decision_history_id"), event.get("evidence_snapshot_id"),
            event.get("symbol"), event.get("decision"), event.get("option_symbol"), event.get("status"),
            event.get("rationale"), event.get("entry_at"), event.get("exit_at"), event.get("entry_price"),
            event.get("exit_price"), event.get("quantity"), event.get("realized_pnl_pct"),
            event.get("realized_pnl_dollars"), event.get("commission_dollars"), event.get("slippage_dollars"),
            json_text(event.get("warnings") or []) or "[]", json_text(event) or "{}",
        ),
    )


def _simulate_decisions(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    *,
    start_at: str | None,
    end_at: str | None,
    limit: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = """
        SELECT history.id, history.symbol, history.decision_run_at, history.manager_decision, history.manager_reason,
               history.selected_option_symbol, history.selected_option_id, payload.manager_input_json,
               payload.manager_output_json, COALESCE(sector.sector_key, 'unknown') AS sector_key
        FROM manager_decision_history AS history
        LEFT JOIN manager_decision_payloads AS payload ON payload.manager_decision_history_id = history.id
        LEFT JOIN companies AS company ON company.id = history.company_id
        LEFT JOIN industries AS industry ON industry.id = company.industry_id
        LEFT JOIN sectors AS sector ON sector.id = industry.sector_id
        WHERE LOWER(history.manager_decision) IN ('call', 'put')
    """
    parameters: list[Any] = []
    if start_at:
        query += " AND history.decision_run_at >= ?"
        parameters.append(normalize_timestamp(start_at))
    if end_at:
        query += " AND history.decision_run_at <= ?"
        parameters.append(normalize_timestamp(end_at))
    available_decision_count = conn.execute(
        f"SELECT COUNT(*) FROM ({query})", tuple(parameters)
    ).fetchone()[0]
    decision_limit = max(1, min(int(limit), 1000)) if limit is not None else None
    if decision_limit is None:
        query += " ORDER BY history.decision_run_at ASC, history.id ASC"
    else:
        # Limit the recent window first, then simulate it in chronological order.
        query += " ORDER BY history.decision_run_at DESC, history.id DESC LIMIT ?"
        parameters.append(decision_limit)
    decisions = [dict(row) for row in conn.execute(query, tuple(parameters)).fetchall()]
    if decision_limit is not None:
        decisions.reverse()
    events, metrics = _simulate_supplied_decisions(conn, decisions, config)
    metrics["available_decision_count"] = available_decision_count
    metrics["decision_limit"] = decision_limit
    metrics["omitted_older_decision_count"] = available_decision_count - len(decisions)
    return events, metrics


def _simulate_supplied_decisions(
    conn: sqlite3.Connection,
    decisions: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for decision in decisions:
        snapshot = ensure_decision_evidence_snapshot(decision, conn=conn)
        events.append(_simulate_decision(decision, snapshot, conn, config))
    _apply_portfolio_limits(events, config)
    metrics = _metrics(events)
    metrics["decision_count"] = len(decisions)
    metrics["decision_start_at"] = decisions[0]["decision_run_at"] if decisions else None
    metrics["decision_end_at"] = decisions[-1]["decision_run_at"] if decisions else None
    metrics["snapshot_count"] = len({event.get("evidence_snapshot_id") for event in events})
    return events, metrics


def simulate_backtest_metrics(
    *,
    config: dict[str, Any] | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    """Simulate every decision in the window like run_backtest, without recording a backtest run."""
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        _, metrics = _simulate_decisions(conn, _merged_config(config), start_at=start_at, end_at=end_at, limit=None)
    return metrics


def run_backtest(
    *,
    config: dict[str, Any] | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int | None = 250,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    def simulate(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return _simulate_decisions(conn, resolved_config, start_at=start_at, end_at=end_at, limit=limit)

    resolved_config = _merged_config(config)
    return _record_backtest_run(resolved_config, simulate, db_path=db_path)


def run_decision_backtest(
    decisions: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    extra_metrics: dict[str, Any] | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    """Simulate supplied decisions, such as a replayed model's, and record them as a backtest run.

    Each decision has the history fields `_simulate_event` reads. One that also carries its
    frozen `selected_contract` and `reference_spot` is model-priced when no quote path exists.
    """
    resolved_config = _merged_config(config)

    def simulate(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        events, metrics = _simulate_supplied_decisions(conn, decisions, resolved_config)
        return events, {**metrics, **(extra_metrics or {})}

    return _record_backtest_run(resolved_config, simulate, db_path=db_path)


def _record_backtest_run(
    resolved_config: dict[str, Any],
    simulate: Callable[[sqlite3.Connection], tuple[list[dict[str, Any]], dict[str, Any]]],
    *,
    db_path: Path | str,
) -> dict[str, Any]:
    initialize_database(db_path=db_path)
    started_at = datetime.now(timezone.utc).isoformat()
    with closing(get_connection(db_path)) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO backtest_runs (
                strategy_version, model, prompt_version, config_json, evidence_snapshot_version,
                data_source_versions_json, code_revision, started_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                resolved_config["strategy_version"], resolved_config["model"], resolved_config["prompt_version"],
                json_text(resolved_config) or "{}", SNAPSHOT_VERSION,
                json_text(resolved_config["data_source_versions"]) or "{}", os.getenv("GIT_COMMIT", ""), started_at,
            ),
        )
        run_id = int(cursor.lastrowid)
        try:
            events, metrics = simulate(conn)
            for event in events:
                _store_event(conn, run_id, event)
            completed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE backtest_runs
                SET status = 'completed', completed_at = ?, metrics_json = ?
                WHERE id = ?
                """,
                (completed_at, json_text(metrics) or "{}", run_id),
            )
        except Exception as exc:
            conn.execute(
                "UPDATE backtest_runs SET status = 'failed', completed_at = ?, error_message = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), str(exc), run_id),
            )
            raise
    return get_backtest_run(run_id, db_path=db_path)


def list_backtest_runs(*, limit: int = 25, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        rows = conn.execute(
            "SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [{**dict(row), "metrics": _json_object(row["metrics_json"]), "config": _json_object(row["config_json"])} for row in rows]


def get_backtest_run(run_id: int, *, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        run = conn.execute("SELECT * FROM backtest_runs WHERE id = ?", (int(run_id),)).fetchone()
        if run is None:
            raise LookupError("Backtest run not found.")
        events = conn.execute(
            "SELECT * FROM backtest_events WHERE run_id = ? ORDER BY as_of ASC, id ASC",
            (int(run_id),),
        ).fetchall()
    run_data = dict(run)
    event_payloads = []
    for event in events:
        event_data = _json_object(event["event_json"])
        event_payloads.append(
            {
                **dict(event),
                "warnings": _json_object(event["warnings_json"]) if str(event["warnings_json"] or "").startswith("{") else json.loads(event["warnings_json"] or "[]"),
                "event": event_data,
                "quote_provider": event_data.get("quote_provider"),
                "pricing_source": event_data.get("pricing_source"),
            }
        )
    return {
        "run": {**run_data, "metrics": _json_object(run_data.get("metrics_json")), "config": _json_object(run_data.get("config_json")), "data_source_versions": _json_object(run_data.get("data_source_versions_json"))},
        "events": event_payloads,
        "read_only": True,
        "no_trade_executed_by_feature": True,
    }
