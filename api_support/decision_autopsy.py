from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api_support.common import safe_float, safe_json_loads
from api_support.context import DB_PATH
from api_support.v2_verdicts import shadow_verdicts_for_decisions
from db_helpers.market import list_decision_autopsy_ledger


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


def _build_review(snapshot: dict[str, Any], row: dict[str, Any]) -> dict[str, str]:
    manager_input = snapshot.get("manager_input") if isinstance(snapshot.get("manager_input"), dict) else {}
    strategist = manager_input.get("strategist_recommendation") if isinstance(manager_input.get("strategist_recommendation"), dict) else {}
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


def _serialize_ledger_row(raw_row: Any) -> dict[str, Any]:
    row = dict(raw_row)
    snapshot = safe_json_loads(row.get("snapshot_json"))
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    manager_input = snapshot.get("manager_input") if isinstance(snapshot.get("manager_input"), dict) else {}
    manager_output = snapshot.get("manager_output") if isinstance(snapshot.get("manager_output"), dict) else {}
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
        "review": _build_review(snapshot, row),
    }


def build_decision_autopsy_payload(*, limit: int = 100) -> dict[str, Any]:
    records = [_serialize_ledger_row(row) for row in list_decision_autopsy_ledger(limit=limit, db_path=DB_PATH)]
    # One query for the page, not one per record. None means the lifecycle
    # tables cannot be read, which a reader must not mistake for "nothing
    # objected to this decision".
    verdicts = shadow_verdicts_for_decisions(
        (record["manager_decision_history_id"] for record in records), db_path=DB_PATH
    )
    for record in records:
        record["v2_verdicts"] = (verdicts or {}).get(record["manager_decision_history_id"], [])
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": records,
        "read_only": True,
        "no_trade_executed_by_feature": True,
        "v2_verdicts_available": verdicts is not None,
    }
