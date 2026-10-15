"""Resolve linked decisions from complete broker round trips, never position marks."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from db_common import DB_PATH
from market_db_modules.history_records import (
    list_unresolved_manager_decisions,
    record_manager_decision_closed_outcome,
)
from services.order_values import normalize_order_summary


TERMINAL_STATUSES = {"filled", "canceled", "expired", "replaced", "rejected"}


def parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def closed_round_trip(orders: list[dict], entry_order_id: str) -> dict | None:
    """Match one entry to its exits; overlapping buys are deliberately unresolved.

    Terminal canceled/replaced orders can contain real partial fills. Require a
    broker fill timestamp for those too; updated_at is not an execution time.
    """
    fills = []
    seen = set()
    for raw in orders:
        order = normalize_order_summary(raw)
        order_id = str(order.get("id") or "")
        if not order_id or order_id in seen:
            continue
        seen.add(order_id)
        try:
            quantity = Decimal(str(order.get("filled_qty") or "0"))
            price = Decimal(str(order.get("filled_avg_price") or "0"))
        except InvalidOperation:
            return None
        if not quantity.is_finite() or not price.is_finite() or quantity < 0:
            return None
        if quantity == 0:
            continue
        timestamp = parse_timestamp(order.get("filled_at"))
        if timestamp is None or price <= 0 or order["status"] not in TERMINAL_STATUSES:
            return None
        fills.append((timestamp, order_id, order["side"], quantity, price))
    fills.sort()
    balance = Decimal(0)
    cost = Decimal(0)
    proceeds = Decimal(0)
    buys = []
    exits = []
    for timestamp, order_id, side, quantity, price in fills:
        if side == "buy":
            buys.append(order_id)
            balance += quantity
            cost += quantity * price
        elif side == "sell":
            # Missing earlier inventory makes the history unsafe to attribute.
            if quantity > balance:
                return None
            balance -= quantity
            proceeds += quantity * price
            exits.append(order_id)
            if balance == 0:
                if entry_order_id in buys:
                    if buys != [entry_order_id]:
                        return None
                    return {
                        "realized_pnl_pct": float((proceeds / cost - 1) * 100),
                        "closed_at": timestamp.isoformat(),
                        "exit_order_ids": exits,
                    }
                cost = proceeds = Decimal(0)
                buys, exits = [], []
        else:
            return None
    return None


def reconcile_closed_outcomes(*, gateway, trading_client, db_path=DB_PATH, apply=True) -> dict:
    decisions = list_unresolved_manager_decisions(db_path=db_path)
    report = {"examined": len(decisions), "resolved": 0, "ready": [], "unresolved": []}
    if not decisions:
        return report
    # A failed positions request must propagate; an error is not an empty book.
    open_symbols = {
        position["option_symbol"]
        for position in gateway.get_open_option_positions(trading_client)
    }
    orders_by_symbol = {}
    for decision in decisions:
        symbol = decision["selected_option_symbol"]
        if symbol in open_symbols:
            report["unresolved"].append({"id": decision["id"], "reason": "position_open"})
            continue
        try:
            if symbol not in orders_by_symbol:
                orders_by_symbol[symbol] = gateway.get_option_order_history(
                    trading_client, option_symbol=symbol,
                )
            outcome = closed_round_trip(
                orders_by_symbol[symbol], decision["trade_execution_order_id"],
            )
            if outcome is None:
                report["unresolved"].append({"id": decision["id"], "reason": "incomplete_or_ambiguous_fills"})
                continue
            report["ready"].append({"id": decision["id"], **outcome})
            pnl = outcome["realized_pnl_pct"]
            label = "closed_profit" if pnl > 0 else "closed_loss" if pnl < 0 else "closed_flat"
            if apply and record_manager_decision_closed_outcome(
                decision_history_id=decision["id"], realized_pnl_pct=pnl,
                closed_at=outcome["closed_at"], resolved_outcome_label=label,
                broker_confirmed=True, closing_order_ids=outcome["exit_order_ids"], db_path=db_path,
            ):
                report["resolved"] += 1
        except Exception as exc:
            report["unresolved"].append({"id": decision["id"], "reason": str(exc)})
    return report
