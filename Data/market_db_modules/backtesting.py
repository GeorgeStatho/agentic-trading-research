from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from db_common import DB_PATH, clean_text, coerce_float, get_connection, json_text, table_exists
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp


SNAPSHOT_VERSION = "decision-evidence-snapshot-v1"
BACKTEST_ENGINE_VERSION = "one-contract-bid-ask-v1"
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
    with get_connection(db_path) as conn:
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
    with get_connection(db_path) as conn:
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
    with get_connection(db_path) as conn:
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
    for key in ("entry_slippage_bps", "exit_slippage_bps", "commission_per_contract", "max_spread_pct", "min_volume", "take_profit_pct", "stop_loss_pct"):
        merged[key] = _number(merged[key])
    for key in ("option_multiplier", "default_order_qty", "max_entry_delay_minutes", "max_hold_hours"):
        merged[key] = max(1, int(_number(merged[key], 1)))
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


def _simulate_event(decision: dict[str, Any], snapshot: dict[str, Any], conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    as_of = _parse_timestamp(decision.get("decision_run_at"))
    option_symbol = str(decision.get("selected_option_symbol") or "").strip().upper()
    quantity = max(1, coerce_int(decision.get("order_qty")) or config["default_order_qty"])
    base_event = {
        "as_of": as_of.isoformat() if as_of is not None else "",
        "manager_decision_history_id": int(decision["id"]),
        "evidence_snapshot_id": int(snapshot["id"]),
        "symbol": str(decision.get("symbol") or "").strip().upper(),
        "decision": str(decision.get("manager_decision") or "").strip().lower(),
        "option_symbol": option_symbol,
        "rationale": str(decision.get("manager_reason") or "").strip(),
        "quantity": quantity,
        "warnings": [],
    }
    if as_of is None or not option_symbol:
        return {**base_event, "status": "no_fill", "warnings": ["missing_decision_timestamp_or_contract"]}
    quotes = [quote for quote in _quote_path(conn, option_symbol, as_of) if _valid_quote(quote, config)]
    if not quotes:
        return {**base_event, "status": "no_fill", "warnings": ["no_valid_historical_entry_quote"]}
    entry_quote = quotes[0]
    entry_at = _parse_timestamp(entry_quote.get("quote_at"))
    if entry_at is None or (entry_at - as_of).total_seconds() > config["max_entry_delay_minutes"] * 60:
        return {**base_event, "status": "no_fill", "warnings": ["next_quote_exceeds_entry_delay"]}
    entry_price = _number(entry_quote.get("ask_price")) * (1 + config["entry_slippage_bps"] / 10_000)
    multiplier = config["option_multiplier"]
    commission = config["commission_per_contract"] * quantity * 2
    entry_notional = entry_price * multiplier * quantity
    pricing_source = _pricing_source(entry_quote)
    cutoff = entry_at + timedelta(hours=config["max_hold_hours"])
    exit_quote: dict[str, Any] | None = None
    exit_price = 0.0
    exit_at: datetime | None = None
    warnings: list[str] = []
    for quote in quotes[1:]:
        quote_at = _parse_timestamp(quote.get("quote_at"))
        if quote_at is None or quote_at > cutoff:
            break
        candidate_exit = _number(quote.get("bid_price")) * (1 - config["exit_slippage_bps"] / 10_000)
        candidate_pnl_pct = ((candidate_exit * multiplier * quantity - entry_notional - commission) / entry_notional) * 100
        exit_quote = quote
        exit_price = candidate_exit
        exit_at = quote_at
        if candidate_pnl_pct >= config["take_profit_pct"]:
            warnings.append("take_profit_exit")
            break
        if candidate_pnl_pct <= config["stop_loss_pct"]:
            warnings.append("stop_loss_exit")
            break
    if exit_quote is None or exit_at is None:
        return {
            **base_event,
            "status": "no_exit_quote",
            "entry_at": entry_at.isoformat(),
            "entry_price": entry_price,
            "quote_provider": entry_quote.get("provider"),
            "pricing_source": pricing_source,
            "warnings": ["no_valid_historical_exit_quote"],
        }
    gross_pnl = (exit_price - entry_price) * multiplier * quantity
    realized_pnl_dollars = gross_pnl - commission
    realized_pnl_pct = (realized_pnl_dollars / entry_notional) * 100 if entry_notional else None
    return {
        **base_event,
        "status": "simulated_closed",
        "entry_at": entry_at.isoformat(),
        "exit_at": exit_at.isoformat(),
        "entry_price": round(entry_price, 6),
        "exit_price": round(exit_price, 6),
        "quote_provider": entry_quote.get("provider"),
        "pricing_source": pricing_source,
        "realized_pnl_pct": round(realized_pnl_pct, 4) if realized_pnl_pct is not None else None,
        "realized_pnl_dollars": round(realized_pnl_dollars, 4),
        "commission_dollars": round(commission, 4),
        "slippage_dollars": round(
            ((_number(entry_quote.get("ask_price")) * config["entry_slippage_bps"] / 10_000)
             + (_number(exit_quote.get("bid_price")) * config["exit_slippage_bps"] / 10_000)) * multiplier * quantity,
            4,
        ),
        "warnings": warnings,
    }


def _metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [event for event in events if event.get("status") == "simulated_closed"]
    no_fill = [event for event in events if event.get("status") == "no_fill"]
    pnl_values = [float(event["realized_pnl_pct"]) for event in filled if event.get("realized_pnl_pct") is not None]
    pnl_dollars = [float(event["realized_pnl_dollars"]) for event in filled if event.get("realized_pnl_dollars") is not None]
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    gross_loss = abs(sum(losses))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    equity_curve = []
    for event in filled:
        cumulative += _number(event.get("realized_pnl_dollars"))
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        equity_curve.append({"at": event.get("exit_at"), "pnl_dollars": round(cumulative, 4)})
    return {
        "event_count": len(events),
        "filled_count": len(filled),
        "no_fill_count": len(no_fill),
        "no_exit_quote_count": len([event for event in events if event.get("status") == "no_exit_quote"]),
        "fill_rate_pct": round((len(filled) / len(events)) * 100, 2) if events else None,
        "win_rate_pct": round((len(wins) / len(pnl_values)) * 100, 2) if pnl_values else None,
        "expectancy_pct": round(sum(pnl_values) / len(pnl_values), 4) if pnl_values else None,
        "median_pnl_pct": round(float(median(pnl_values)), 4) if pnl_values else None,
        "profit_factor_like": round(sum(wins) / gross_loss, 4) if gross_loss else None,
        "total_pnl_dollars": round(sum(pnl_dollars), 4),
        "total_commission_dollars": round(sum(_number(event.get("commission_dollars")) for event in filled), 4),
        "total_slippage_dollars": round(sum(_number(event.get("slippage_dollars")) for event in filled), 4),
        "max_drawdown_dollars": round(max_drawdown, 4),
        "equity_curve": equity_curve,
    }


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


def run_backtest(
    *,
    config: dict[str, Any] | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int = 250,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    initialize_database(db_path=db_path)
    resolved_config = _merged_config(config)
    started_at = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
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
            query = """
                SELECT id, symbol, decision_run_at, manager_decision, manager_reason,
                       selected_option_symbol, selected_option_id, manager_input_json, manager_output_json
                FROM manager_decision_history
                WHERE LOWER(manager_decision) IN ('call', 'put')
            """
            parameters: list[Any] = []
            if start_at:
                query += " AND decision_run_at >= ?"
                parameters.append(normalize_timestamp(start_at))
            if end_at:
                query += " AND decision_run_at <= ?"
                parameters.append(normalize_timestamp(end_at))
            query += " ORDER BY decision_run_at ASC, id ASC LIMIT ?"
            parameters.append(max(1, min(int(limit), 1000)))
            decisions = [dict(row) for row in conn.execute(query, tuple(parameters)).fetchall()]
            events: list[dict[str, Any]] = []
            for decision in decisions:
                snapshot = ensure_decision_evidence_snapshot(decision, conn=conn)
                event = _simulate_event(decision, snapshot, conn, resolved_config)
                _store_event(conn, run_id, event)
                events.append(event)
            metrics = _metrics(events)
            metrics["decision_count"] = len(decisions)
            metrics["snapshot_count"] = len({event.get("evidence_snapshot_id") for event in events})
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
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [{**dict(row), "metrics": _json_object(row["metrics_json"]), "config": _json_object(row["config_json"])} for row in rows]


def get_backtest_run(run_id: int, *, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
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
