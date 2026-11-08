from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
from api_support.context import OPTION_POSITION_SETTINGS, logger
from api_support.request_utils import safe_json_loads
from db_common import get_connection
from services.core.numbers import safe_float
from api_support.v2_verdicts import shadow_verdicts_for_decisions
from market_db_modules.autopsy import list_decision_autopsy_ledger
# The engine's own pricing steps, used read-only: its public entry points record a run and
# snapshots. Kept here, off the trading path, so a dashboard deploy leaves research evidence valid.
from market_db_modules.backtesting import _merged_config, _simulate_decision
from trading_support._option_positions_defaults import build_option_exit_rules
from trading_support._option_positions_market import _resolve_option_exit_thresholds

EXIT_REASONS = {"take_profit_exit": "take_profit", "stop_loss_exit": "stop_loss"}
MARKET_TIMEZONE = ZoneInfo("America/New_York")


def counterfactual_trade(
    conn: sqlite3.Connection,
    decision: dict[str, Any],
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> dict[str, Any]:
    """What a call or put that never became an order would have done, priced like a backtest.

    Read-only: unlike a backtest it records no evidence snapshot or run, so a page can ask on
    every load. The premium cap is not applied, since the question is the trade itself.
    """
    config = _merged_config({"take_profit_pct": take_profit_pct, "stop_loss_pct": stop_loss_pct,
                             "max_hold_hours": max_hold_hours})
    event = _simulate_decision({k: v for k, v in decision.items() if k != "max_premium_dollars"}, {"id": 0}, conn, config)
    event.pop("evidence_snapshot_id", None)
    return event


def market_day_bounds(market_day: date) -> tuple[str, str]:
    """A New York trading day as UTC ISO bounds, [start, end); stored run times are UTC ISO text, so they compare as strings."""
    start = datetime.combine(market_day, time(0), MARKET_TIMEZONE)
    end = datetime.combine(market_day + timedelta(days=1), time(0), MARKET_TIMEZONE)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def decision_funnel(market_day: date) -> dict[str, Any]:
    """How far every decision of one New York trading day got, not only the loaded page."""
    with closing(get_connection()) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS evaluated,
                   COALESCE(SUM(LOWER(history.strategist_decision) = 'trade_candidate'), 0) AS trade_candidates,
                   COALESCE(SUM(LOWER(history.manager_decision) = 'call'), 0) AS call,
                   COALESCE(SUM(LOWER(history.manager_decision) = 'put'), 0) AS put,
                   COALESCE(SUM(history.trade_executed = 1), 0) AS executed
            FROM decision_autopsy_ledger AS ledger
            JOIN manager_decision_history AS history ON history.id = ledger.manager_decision_history_id
            WHERE ledger.decision_run_at >= ? AND ledger.decision_run_at < ?
            """,
            market_day_bounds(market_day),
        ).fetchone()
    return {"market_day": market_day.isoformat(), **{key: int(row[key]) for key in ("evaluated", "trade_candidates", "call", "put", "executed")}}


def todays_decision_funnel(*, now: datetime) -> dict[str, Any]:
    """The funnel for the New York trading day `now` falls on."""
    return decision_funnel(now.astimezone(MARKET_TIMEZONE).date())


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _article_references(manager_input: dict[str, Any]) -> list[dict[str, Any]]:
    references = manager_input.get("article_references")
    if not isinstance(references, list):
        return []
    return [
        {
            "article_id": int(item.get("article_id") or 0),
            "title": str(item.get("title") or "").strip(),
            "source": str(item.get("source") or "").strip(),
            "published_at": str(item.get("published_at") or "").strip(),
            "article_scope": str(item.get("article_scope") or "").strip(),
            "evidence_layers": _string_list(item.get("evidence_layers")),
        }
        for item in references
        if isinstance(item, dict)
    ]


def _json_dict(value: Any) -> dict[str, Any]:
    parsed = safe_json_loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _build_review(snapshot: dict[str, Any], strategist: dict[str, Any], row: dict[str, Any]) -> dict[str, str]:
    risks = _string_list(strategist.get("risks"))
    pnl = safe_float(row.get("latest_trade_pnl_pct"))
    decision = str(snapshot.get("manager_decision") or "").strip() or "No trade"
    if not bool(row.get("trade_executed")):
        return {
            "status": "not_executed",
            "headline": "No order was executed.",
            "what_proved_us_wrong": "No live trade outcome exists; this proposal remains an evidence and constraint record only.",
        }
    if pnl is None:
        return {
            "status": "outcome_pending",
            "headline": "Outcome is still pending.",
            "what_proved_us_wrong": "The order was executed, but no eligible P/L outcome has been recorded yet.",
        }
    if pnl < 0:
        return {
            "status": "adverse_outcome",
            "headline": f"The executed {decision.lower()} is currently {pnl:.2f}%.",
            "what_proved_us_wrong": risks[0] if risks else "The expected directional scenario did not hold after execution.",
        }
    return {
        "status": "favorable_outcome",
        "headline": f"The executed {decision.lower()} is currently {pnl:.2f}%.",
        "what_proved_us_wrong": "No adverse evidence has been recorded in the eligible outcome window; continue to treat the original risks as live constraints.",
    }


def _execution_mode(row: dict[str, Any]) -> str:
    paper = row.get("execution_paper")
    if paper is None:
        return "unlinked"
    return "paper" if bool(paper) else "live"


def _attribution(row: dict[str, Any]) -> dict[str, Any] | None:
    if not row.get("primary_failure"):
        return None
    horizon_returns = _json_dict(row.get("attribution_json")).get("direction", {}).get("horizon_returns")
    return {
        **{key: row[key] for key in ("evidence_quality", "evidence_source", "direction_outcome", "expression_outcome", "primary_failure")},
        "horizon_returns": horizon_returns or {},
    }


def _decided_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def counterfactual_for_row(conn: sqlite3.Connection, row: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    """The contract a blocked call or put had picked, priced as if bought, under the exit rule for its DTE.

    None when there is nothing to price: the trade went through, the manager chose no
    direction, or no contract was selected.
    """
    if row.get("trade_executed"):
        return None
    snapshot = _json_dict(row.get("snapshot_json"))
    manager_output = _json_dict(row.get("manager_output_json"))
    recommendation = manager_output.get("recommendation") if isinstance(manager_output.get("recommendation"), dict) else {}
    decision = str(snapshot.get("manager_decision") or recommendation.get("decision") or "").strip().lower()
    contract = manager_output.get("selected_option")
    decided_at = _decided_at(snapshot.get("decision_run_at") or row.get("decision_run_at"))
    if decision not in {"call", "put"} or not isinstance(contract, dict) or not contract.get("symbol") or decided_at is None:
        return None
    dte = safe_float(contract.get("days_to_expiration"))
    if dte is None:
        return None
    rule = _resolve_option_exit_thresholds(
        days_to_expiration=int(dte),
        default_take_profit_pct=OPTION_POSITION_SETTINGS.take_profit_pct,
        default_stop_loss_pct=OPTION_POSITION_SETTINGS.stop_loss_pct,
        default_exit_hours_to_expiration=0.0,
        exit_rules=build_option_exit_rules(OPTION_POSITION_SETTINGS.dte_rules),
    )
    # Held until the rule's forced exit before expiry, as the option manager would.
    hold = timedelta(days=max(1.0, dte - (rule.force_exit_days_to_expiration or 0)))
    market_context = _json_dict(row.get("manager_input_json")).get("market_context")
    event = counterfactual_trade(
        conn,
        {
            "id": int(row.get("manager_decision_history_id") or 0),
            "symbol": str(snapshot.get("symbol") or row.get("symbol") or "").strip().upper(),
            "decision_run_at": decided_at.isoformat(),
            "manager_decision": decision,
            "manager_reason": str(recommendation.get("reason") or ""),
            "selected_option_symbol": str(contract["symbol"]).strip().upper(),
            "selected_contract": contract,
            "reference_spot": _get_reference_stock_price(market_context) if isinstance(market_context, dict) else None,
        },
        take_profit_pct=rule.take_profit_pct,
        stop_loss_pct=rule.stop_loss_pct,
        max_hold_hours=int(hold.total_seconds() // 3600),
    )
    warnings = list(event.get("warnings") or [])
    closed = event.get("status") == "simulated_closed"
    # Neither threshold before the last quote we hold: quotes are fetched for about a week after
    # each decision, so this is often the end of the data rather than the end of the window.
    exit_reason = next((EXIT_REASONS[warning] for warning in warnings if warning in EXIT_REASONS), "last_mark" if closed else None)
    return {
        "option_symbol": event.get("option_symbol"),
        "status": event.get("status"),
        "entry_at": event.get("entry_at"),
        "entry_price": event.get("entry_price"),
        "exit_at": event.get("exit_at"),
        "exit_price": event.get("exit_price"),
        "realized_pnl_pct": event.get("realized_pnl_pct"),
        "realized_pnl_dollars": event.get("realized_pnl_dollars"),
        "pricing_source": event.get("pricing_source"),
        "exit_reason": exit_reason,
        # Not a closed trade yet: the window runs on, so this is the latest close, not the outcome.
        "still_open": exit_reason == "last_mark" and decided_at + hold > now,
        "take_profit_pct": rule.take_profit_pct,
        "stop_loss_pct": rule.stop_loss_pct,
        "dte_bucket": rule.dte_rule_label,
        "warnings": warnings,
    }


def _serialize_ledger_row(raw_row: Any) -> dict[str, Any]:
    row = dict(raw_row)
    snapshot = _json_dict(row.get("snapshot_json"))
    manager_input = _json_dict(row.get("manager_input_json"))
    manager_output = _json_dict(row.get("manager_output_json"))
    strategist = manager_input.get("strategist_recommendation") if isinstance(manager_input.get("strategist_recommendation"), dict) else {}
    recommendation = manager_output.get("recommendation") if isinstance(manager_output.get("recommendation"), dict) else {}
    entry_gate = manager_output.get("entry_gate") if isinstance(manager_output.get("entry_gate"), dict) else {}
    market_regime_gate = manager_output.get("market_regime_gate") if isinstance(manager_output.get("market_regime_gate"), dict) else {}
    market_context = manager_input.get("market_context") if isinstance(manager_input.get("market_context"), dict) else {}
    constraints = {
        "target_dte_bucket": str(snapshot.get("target_dte_bucket") or "").strip(),
        "selected_option_id": str(snapshot.get("selected_option_id") or "").strip(),
        "selected_option_symbol": str(snapshot.get("selected_option_symbol") or "").strip(),
        "selected_expiration_date": str(snapshot.get("selected_expiration_date") or "").strip(),
        "selected_strike_price": safe_float(snapshot.get("selected_strike_price")),
        "selected_option_source": str(snapshot.get("selected_option_source") or "").strip(),
        "account_state": market_context.get("account_state", {}),
        "option_market_summary": market_context.get("option_market", {}),
        "selection_debug": recommendation.get("selection_debug", {}),
        "entry_gate": entry_gate,
        "market_regime": market_context.get("market_regime", {}),
        "market_regime_gate": market_regime_gate,
    }
    return {
        "id": int(row.get("id") or 0),
        "manager_decision_history_id": int(row.get("manager_decision_history_id") or 0),
        "symbol": str(snapshot.get("symbol") or row.get("symbol") or "").strip().upper(),
        "company_name": str(snapshot.get("company_name") or "").strip(),
        "decision_run_at": str(snapshot.get("decision_run_at") or row.get("decision_run_at") or "").strip(),
        "created_at": str(row.get("created_at") or "").strip(),
        "strategist": {
            "decision": str(snapshot.get("strategist_decision") or strategist.get("decision") or "").strip(),
            "confidence": str(strategist.get("confidence") or "").strip(),
            "model": strategist.get("strategist_run_reference", {}).get("model") if isinstance(strategist.get("strategist_run_reference"), dict) else "",
            "summary": str(strategist.get("summary") or "").strip(),
            "thesis": _string_list(strategist.get("thesis")),
            "risks": _string_list(strategist.get("risks")),
            "evidence_claims": strategist.get("evidence_claims", []) if isinstance(strategist.get("evidence_claims"), list) else [],
        },
        "manager": {
            "decision": str(snapshot.get("manager_decision") or recommendation.get("decision") or "").strip(),
            "confidence": str(snapshot.get("manager_confidence") or recommendation.get("confidence") or "").strip(),
            "reason": str(snapshot.get("manager_reason") or recommendation.get("reason") or "").strip(),
        },
        "evidence": _article_references(manager_input),
        "constraints": constraints,
        "outcome": {
            "trade_executed": bool(row.get("trade_executed")),
            "order_id": str(row.get("trade_execution_order_id") or "").strip(),
            "pnl_pct": safe_float(row.get("latest_trade_pnl_pct")),
            "realized_pnl_pct": safe_float(row.get("realized_pnl_pct")),
            "closed_at": row.get("closed_at"),
            "outcome_status": row.get("outcome_status"),
            "exit_reason": row.get("exit_reason"),
            "pnl_updated_at": str(row.get("latest_trade_pnl_updated_at") or "").strip(),
            "resolved_outcome_label": str(row.get("resolved_outcome_label") or "").strip(),
        },
        "execution": {
            "record_id": int(row.get("trade_execution_record_id") or 0) or None,
            "mode": _execution_mode(row),
            "paper": bool(row.get("execution_paper")) if row.get("execution_paper") is not None else None,
            "status": str(row.get("execution_order_status") or "").strip(),
            "option_symbol": str(row.get("execution_option_symbol") or "").strip(),
            "underlying_symbol": str(row.get("execution_underlying_symbol") or "").strip(),
            "quantity": safe_float(row.get("execution_order_qty")),
            "side": str(row.get("execution_order_side") or "").strip(),
            "submitted_at": str(row.get("execution_submitted_at") or "").strip(),
        },
        "review": _build_review(snapshot, strategist, row),
        "attribution": _attribution(row),
    }


BENCHMARK_COLUMNS = ("underlying_move", "spy_return", "sector_return", "residual_move", "iv_at_entry",
                     "iv_at_exit", "return_1d_after_exit", "return_3d_after_exit")


def _benchmarks_for_decisions(decision_ids: list[int]) -> dict[int, dict[str, Any]]:
    """How each traded decision's position moved against SPY and its sector; one query per page."""
    if not decision_ids:
        return {}
    with closing(get_connection()) as conn:
        rows = conn.execute(
            f"SELECT manager_decision_history_id, {', '.join(BENCHMARK_COLUMNS)} FROM position_outcomes "
            f"WHERE manager_decision_history_id IN ({', '.join('?' * len(decision_ids))})",
            decision_ids,
        ).fetchall()
    return {row["manager_decision_history_id"]: {column: row[column] for column in BENCHMARK_COLUMNS} for row in rows}


def build_decision_autopsy_payload(*, limit: int = 100, day: date | None = None) -> dict[str, Any]:
    """The newest `limit` decisions, or with `day` the newest `limit` of that New York trading day."""
    since, until = market_day_bounds(day) if day is not None else (None, None)
    rows = [dict(row) for row in list_decision_autopsy_ledger(limit=limit, since=since, as_of=until)]
    records = [_serialize_ledger_row(row) for row in rows]
    now = datetime.now(timezone.utc)
    with closing(get_connection()) as conn:
        for record, row in zip(records, rows):
            try:
                record["counterfactual"] = counterfactual_for_row(conn, row, now=now)
            except Exception:  # one unpriceable record must not take down the ledger
                logger.warning("Counterfactual pricing failed for decision %s", record["manager_decision_history_id"], exc_info=True)
                record["counterfactual"] = None
    # One query for the page, not one per record. None means the lifecycle
    # tables cannot be read, which a reader must not mistake for "nothing
    # objected to this decision".
    verdicts = shadow_verdicts_for_decisions(
        (record["manager_decision_history_id"] for record in records)
    )
    benchmarks = _benchmarks_for_decisions([record["manager_decision_history_id"] for record in records])
    for record in records:
        record["v2_verdicts"] = (verdicts or {}).get(record["manager_decision_history_id"], [])
        record["benchmark"] = benchmarks.get(record["manager_decision_history_id"])
    return {
        "as_of": now.isoformat(),
        "count": len(records),
        "records": records,
        "today": todays_decision_funnel(now=now),
        **({"day": decision_funnel(day)} if day is not None else {}),
        "read_only": True,
        "no_trade_executed_by_feature": True,
        "v2_verdicts_available": verdicts is not None,
    }
