from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api_support.common import safe_float, safe_json_loads
from api_support.context import (
    DB_PATH,
    SELECTED_OPTIONS_OUTPUT_PATH,
    TRADE_EXECUTION_OUTPUT_PATH,
    initialize_market_database,
    list_option_trade_executions,
)


def load_output_payload(path) -> dict:
    from api_support.common import read_json_payload

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
    }


def build_executed_trades_payload(
    *,
    underlying_symbol: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    initialize_market_database(db_path=DB_PATH)
    rows = list_option_trade_executions(
        underlying_symbol=underlying_symbol,
        limit=limit,
        db_path=DB_PATH,
    )

    trades: list[dict[str, Any]] = []
    for row in rows:
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
                "execution": raw_execution if isinstance(raw_execution, dict) else None,
            }
        )

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "db_path": str(DB_PATH),
        "underlying_symbol": str(underlying_symbol or "").strip().upper(),
        "limit": max(1, int(limit)),
        "count": len(trades),
        "trades": trades,
    }
