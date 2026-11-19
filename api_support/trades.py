from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from api_support.request_utils import safe_json_loads
from services.core.numbers import safe_float
from db_common import current_database, get_connection
from api_support.context import OPTION_POSITION_SETTINGS, SELECTED_OPTIONS_OUTPUT_PATH, TRADE_EXECUTION_OUTPUT_PATH
from market_db_modules.queries import list_option_trade_executions
from trading_support._option_positions_defaults import build_option_exit_rules
from trading_support._option_positions_market import _resolve_option_exit_thresholds

EXECUTED_TRADES_MAX_AGE_DAYS = 30


def load_output_payload(path) -> dict:
    from api_support.request_utils import read_json_payload

    payload = read_json_payload(path)
    return payload if isinstance(payload, dict) else {}


def humanize_rejection_reason(error_value: object) -> str:
    import json

    error_text = str(error_value or "").strip()
    if not error_text:
        return ""

    try:
        parsed = json.loads(error_text)
        if isinstance(parsed, dict):
            message = str(parsed.get("message") or "").strip()
            normalized_message = message.lower()
            if "market hours" in normalized_message:
                return "Market closed"
            return message or error_text
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    normalized_error = error_text.lower()
    if "market hours" in normalized_error:
        return "Market closed"
    return error_text


def format_decision_label(decision: str) -> str:
    normalized = str(decision or "").strip().lower()
    if normalized == "call":
        return "Buy Call"
    if normalized == "put":
        return "Buy Put"
    if normalized in {"neither", "do_not_trade"}:
        return "No Trade"
    return normalized.replace("_", " ").title() if normalized else "No Trade"


def format_selected_contract(execution: dict) -> str:
    selected_option = execution.get("selected_option")
    if not isinstance(selected_option, dict):
        return ""

    contract_type = str(
        selected_option.get("contract_type")
        or execution.get("decision")
        or ""
    ).strip().lower()
    contract_letter = "C" if contract_type == "call" else "P" if contract_type == "put" else ""
    strike = safe_float(
        selected_option.get("strike_price") or execution.get("selected_strike_price")
    )
    expiration = str(
        selected_option.get("expiration_date")
        or execution.get("selected_expiration_date")
        or ""
    ).strip()

    if strike is None or not expiration:
        return str(selected_option.get("symbol") or "").strip().upper()

    strike_text = str(int(strike)) if float(strike).is_integer() else f"{strike:.2f}".rstrip("0").rstrip(".")
    return f"{strike_text}{contract_letter} {expiration}"


def _stopped_at(execution: dict) -> str | None:
    """Which stage kept the latest decision from becoming an order, if one did."""
    if execution.get("submitted"):
        return None
    if str(execution.get("decision") or "").strip().lower() in {"call", "put"}:
        return "safeguard"
    strategist = execution.get("strategist_recommendation")
    strategist_decision = str(strategist.get("decision") or "") if isinstance(strategist, dict) else ""
    return "strategist" if strategist_decision and strategist_decision != "trade_candidate" else "manager"


def _selected_contract_details(execution: dict) -> dict | None:
    option = execution.get("selected_option")
    if not isinstance(option, dict):
        return None
    symbol = str(option.get("symbol") or execution.get("selected_option_symbol") or "").strip().upper()
    dte = safe_float(option.get("days_to_expiration"))
    if not symbol or dte is None:
        return None
    return {
        "symbol": symbol,
        "type": str(option.get("contract_type") or execution.get("decision") or "").strip().lower(),
        "expiration_date": str(option.get("expiration_date") or execution.get("selected_expiration_date") or "").strip(),
        "dte": int(dte),
        "strike": safe_float(option.get("strike_price") or execution.get("selected_strike_price")),
    }


def _exit_plan(dte: int) -> dict:
    """The take-profit and stop-loss the option manager applies at this DTE."""
    thresholds = _resolve_option_exit_thresholds(
        days_to_expiration=dte,
        default_take_profit_pct=OPTION_POSITION_SETTINGS.take_profit_pct,
        default_stop_loss_pct=OPTION_POSITION_SETTINGS.stop_loss_pct,
        default_exit_hours_to_expiration=0.0,
        exit_rules=build_option_exit_rules(OPTION_POSITION_SETTINGS.dte_rules),
    )
    return {"take_profit_pct": thresholds.take_profit_pct, "stop_loss_pct": thresholds.stop_loss_pct, "dte_bucket": thresholds.dte_rule_label}


def _recorded_fill_price(order_id: str) -> float | None:
    if not order_id:
        return None
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT fill_price FROM option_trade_executions WHERE order_id = ?", (order_id,)).fetchone()
    return safe_float(row["fill_price"]) if row else None


def _last_traded_contract() -> str:
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT option_symbol FROM option_trade_executions WHERE order_side = 'buy' AND order_status = 'filled' "
            "ORDER BY submitted_at DESC, id DESC LIMIT 1"
        ).fetchone()
    return str(row["option_symbol"]) if row else ""


def build_trade_explanation_payload() -> dict:
    trade_output = load_output_payload(TRADE_EXECUTION_OUTPUT_PATH)
    selected_output = load_output_payload(SELECTED_OPTIONS_OUTPUT_PATH)

    executions = trade_output.get("executions")
    normalized_executions = executions if isinstance(executions, list) else []
    selected_companies = selected_output.get("companies")
    normalized_selected_companies = (
        selected_companies if isinstance(selected_companies, list) else []
    )

    chosen_execution = None
    if normalized_executions:
        submitted_execution = next(
            (execution for execution in normalized_executions if isinstance(execution, dict) and execution.get("submitted")),
            None,
        )
        chosen_execution = submitted_execution or next(
            (execution for execution in normalized_executions if isinstance(execution, dict)),
            None,
        )
    elif normalized_selected_companies:
        chosen_execution = next(
            (company for company in normalized_selected_companies if isinstance(company, dict)),
            None,
        )

    if not isinstance(chosen_execution, dict):
        return {
            "ran_at": str(trade_output.get("ran_at") or selected_output.get("ran_at") or ""),
            "has_decision": False,
            "ticker": "",
            "decision": "No Trade",
            "confidence": "",
            "reason": "No recent manager decision is available yet.",
            "selected_contract": "",
            "rejected_because": "",
            "submitted": False,
        }

    rejected_because = humanize_rejection_reason(chosen_execution.get("error"))
    contract = _selected_contract_details(chosen_execution)
    order = chosen_execution.get("order")
    strategist = chosen_execution.get("strategist_recommendation")
    return {
        "ran_at": str(trade_output.get("ran_at") or selected_output.get("ran_at") or ""),
        "has_decision": True,
        "ticker": str(chosen_execution.get("symbol") or "").strip().upper(),
        "decision": format_decision_label(str(chosen_execution.get("decision") or "")),
        "confidence": str(chosen_execution.get("confidence") or "").strip().lower(),
        "reason": str(chosen_execution.get("reason") or "").strip(),
        "selected_contract": format_selected_contract(chosen_execution),
        "rejected_because": rejected_because,
        "submitted": bool(chosen_execution.get("submitted")),
        "company_name": str(chosen_execution.get("name") or "").strip(),
        "strategist_decision": str(strategist.get("decision") or "").strip() if isinstance(strategist, dict) else "",
        "stopped_at": _stopped_at(chosen_execution),
        "contract": contract,
        "quantity": int(safe_float(chosen_execution.get("order_qty")) or 0) or None,
        "fill_price": _recorded_fill_price(str(order.get("id") or "") if isinstance(order, dict) else ""),
        "exit_plan": _exit_plan(contract["dte"]) if contract else None,
        "last_traded_contract": _last_traded_contract(),
    }


def _link_positions(trades: list[dict[str, Any]]) -> None:
    """Sets `position_id`: the order id of the filled buy that opened the position.

    Exits record no link to their entry, so a sell is paired with the latest
    filled buy of the same contract before it.
    shortcut: only the loaded rows are searched, so an exit whose entry is
    older than the window stays unpaired (None); store the link at exit time
    if that starts to matter.
    """
    open_entry: dict[str, str] = {}
    for trade in sorted(trades, key=lambda item: (str(item["submitted_at"] or ""), item["id"])):
        symbol = str(trade["option_symbol"] or "")
        if str(trade["order_side"] or "").lower() == "buy":
            filled = str(trade["order_status"] or "").lower() == "filled"
            trade["position_id"] = trade["order_id"] if filled else None
            if filled:
                open_entry[symbol] = trade["order_id"]
        else:
            trade["position_id"] = open_entry.get(symbol)


def _recorded_fills(row_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Fill price and quantity per execution row.

    Read here rather than added to the shared executions query, which sits on the trading path:
    changing it would invalidate research evidence on a dashboard-only deploy.
    """
    if not row_ids:
        return {}
    placeholders = ",".join("?" for _ in row_ids)
    with closing(get_connection()) as conn:
        rows = conn.execute(
            f"SELECT id, fill_price, filled_qty FROM option_trade_executions WHERE id IN ({placeholders})", row_ids
        ).fetchall()
    return {row["id"]: dict(row) for row in rows}


ARTICLE_TEXT_KEYS = frozenset({"body", "raw_json"})


def _without_article_text(raw_execution: Any) -> dict[str, Any] | None:
    """The stored order record minus its cited articles' scraped text, which is the publishers' and never leaves."""
    if not isinstance(raw_execution, dict):
        return None
    context = raw_execution.get("candidate_context")
    if not isinstance(context, dict) or not isinstance(context.get("evidence"), list):
        return raw_execution
    evidence = [
        {key: value for key, value in item.items() if key not in ARTICLE_TEXT_KEYS} if isinstance(item, dict) else item
        for item in context["evidence"]
    ]
    return {**raw_execution, "candidate_context": {**context, "evidence": evidence}}


def build_executed_trades_payload(
    *,
    underlying_symbol: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    rows = list_option_trade_executions(
        underlying_symbol=underlying_symbol,
        within_days=EXECUTED_TRADES_MAX_AGE_DAYS,
        limit=limit,
    )
    fills = _recorded_fills([row["id"] for row in rows])

    trades: list[dict[str, Any]] = []
    for row in rows:
        fill = fills.get(row["id"], {})
        raw_execution = safe_json_loads(row["raw_json"])
        trades.append(
            {
                "id": row["id"],
                "company_id": row["company_id"],
                "order_id": row["order_id"],
                "underlying_symbol": row["underlying_symbol"],
                "company_name": row["company_name"],
                "option_symbol": row["option_symbol"],
                "decision": row["decision"],
                "confidence": row["confidence"],
                "selected_option_id": row["selected_option_id"],
                "selected_option_source": row["selected_option_source"],
                "expiration_date": row["expiration_date"],
                "strike_price": row["strike_price"],
                "order_qty": row["order_qty"],
                "estimated_order_cost": row["estimated_order_cost"],
                "available_buying_power": row["available_buying_power"],
                "max_deployable_buying_power": row["max_deployable_buying_power"],
                "remaining_deployable_buying_power": row["remaining_deployable_buying_power"],
                "paper": bool(row["paper"]),
                "order_status": row["order_status"],
                "order_side": row["order_side"],
                "order_type": row["order_type"],
                "time_in_force": row["time_in_force"],
                "submitted_at": row["submitted_at"],
                "recorded_at": row["recorded_at"],
                "fill_price": fill.get("fill_price"),
                "filled_qty": fill.get("filled_qty"),
                "execution": _without_article_text(raw_execution),
            }
        )
    _link_positions(trades)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "db_path": str(current_database().path),
        "underlying_symbol": str(underlying_symbol or "").strip().upper(),
        "limit": max(1, int(limit)),
        "count": len(trades),
        "trades": trades,
    }
