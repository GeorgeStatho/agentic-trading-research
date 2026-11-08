"""Open broker positions with option quotes, marks and exit-rule protection, for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from api_support.alpaca import alpaca_data_get_json, alpaca_get_json
from api_support.request_utils import read_json_payload
from services.core.numbers import safe_float
from api_support.context import (
    OPTION_POSITION_MANAGEMENT_OUTPUT_PATH,
)
from api_support.thesis_validator import load_latest_thesis_verdicts
from services.core.time_utils import parse_timestamp


def looks_like_option_symbol(symbol: str, asset_class: str = "") -> bool:
    normalized_asset_class = str(asset_class or "").strip().lower()
    if "option" in normalized_asset_class:
        return True
    return bool(re.fullmatch(r"[A-Z]+\d{6}[CP]\d{8}", str(symbol or "").strip().upper()))


def parse_option_symbol(symbol: str) -> dict[str, object]:
    normalized_symbol = str(symbol or "").strip().upper()
    match = re.fullmatch(r"([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})", normalized_symbol)
    if not match:
        return {
            "underlying_symbol": normalized_symbol,
            "contract_type": "",
            "expiration_date": "",
            "strike": None,
        }

    underlying, year, month, day, contract_type, strike_text = match.groups()
    strike = int(strike_text) / 1000.0
    return {
        "underlying_symbol": underlying,
        "contract_type": "call" if contract_type == "C" else "put",
        "expiration_date": f"20{year}-{month}-{day}",
        "strike": strike,
    }


def compute_days_to_expiration(expiration_date_text: str | None) -> float | None:
    if not expiration_date_text:
        return None

    parsed_expiration = parse_timestamp(str(expiration_date_text))
    if parsed_expiration is None:
        return None

    expiration_date = parsed_expiration.date()
    current_date = datetime.now(timezone.utc).date()
    return float((expiration_date - current_date).days)


def load_option_management_snapshot() -> tuple[dict[str, dict], dict]:
    payload = read_json_payload(OPTION_POSITION_MANAGEMENT_OUTPUT_PATH)
    if not isinstance(payload, dict):
        return {}, {}

    positions = payload.get("positions")
    if not isinstance(positions, list):
        return {}, payload

    by_symbol: dict[str, dict] = {}
    for entry in positions:
        if not isinstance(entry, dict):
            continue
        symbol = str(entry.get("symbol") or "").strip().upper()
        if symbol:
            by_symbol[symbol] = entry

    return by_symbol, payload


def extract_option_quote_fields(quote_payload: dict) -> dict[str, float | str | None]:
    if not isinstance(quote_payload, dict):
        return {"bid_price": None, "ask_price": None, "timestamp": ""}

    bid_price = safe_float(
        quote_payload.get("bid_price")
        or quote_payload.get("bp")
    )
    ask_price = safe_float(
        quote_payload.get("ask_price")
        or quote_payload.get("ap")
    )
    timestamp = str(
        quote_payload.get("timestamp")
        or quote_payload.get("t")
        or ""
    )
    return {
        "bid_price": bid_price,
        "ask_price": ask_price,
        "timestamp": timestamp,
    }


def get_latest_option_quote(symbol: str, quote_cache: dict[str, dict]) -> dict[str, float | str | None]:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return {"bid_price": None, "ask_price": None, "timestamp": ""}

    if normalized_symbol in quote_cache:
        return quote_cache[normalized_symbol]

    fallback = {"bid_price": None, "ask_price": None, "timestamp": ""}
    try:
        payload = alpaca_data_get_json(
            "/v1beta1/options/quotes/latest",
            {"symbols": normalized_symbol},
        )
    except Exception:
        quote_cache[normalized_symbol] = fallback
        return fallback

    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    if not isinstance(quotes, dict):
        quote_cache[normalized_symbol] = fallback
        return fallback

    quote = quotes.get(normalized_symbol)
    extracted = extract_option_quote_fields(quote)
    quote_cache[normalized_symbol] = extracted
    return extracted


def compute_option_unrealized_pl_pct(
    entry_price: float | None,
    current_mark_price: float | None,
    fallback_unrealized_pl_pct: float | None,
) -> float | None:
    if entry_price is not None and entry_price > 0 and current_mark_price is not None:
        return round(((current_mark_price - entry_price) / entry_price) * 100.0, 4)
    return fallback_unrealized_pl_pct


def resolve_option_mark_price(
    *,
    raw_position: dict,
    current_bid: float | None,
    current_ask: float | None,
    snapshot: dict,
) -> float | None:
    broker_mark_price = safe_float(raw_position.get("current_price"))
    if broker_mark_price is not None:
        return broker_mark_price
    if current_bid is not None:
        return current_bid
    if current_bid is not None and current_ask is not None:
        return round((current_bid + current_ask) / 2.0, 4)
    snapshot_mark_price = safe_float(snapshot.get("mark_price"))
    if snapshot_mark_price is not None:
        return snapshot_mark_price
    return safe_float(snapshot.get("mid_price"))


def format_exit_rule_status(position_summary: dict, management_payload: dict) -> str:
    reasons = position_summary.get("decision_reasons")
    normalized_reasons = reasons if isinstance(reasons, list) else []
    reason_text = " ".join(str(reason) for reason in normalized_reasons).lower()
    decision = str(position_summary.get("decision") or "").strip().lower()

    if "stop-loss" in reason_text:
        return "Stop loss triggered" if decision == "sell" else "Near stop loss"
    if "take-profit" in reason_text:
        return "Take profit triggered" if decision == "sell" else "Near take profit"
    if "expiration" in reason_text or "hours to expiration" in reason_text:
        return "Near expiration"

    unrealized_pl_pct = safe_float(position_summary.get("unrealized_pl_pct"))
    stop_loss_pct = safe_float(position_summary.get("stop_loss_pct"))
    if stop_loss_pct is None:
        stop_loss_pct = safe_float(management_payload.get("stop_loss_pct"))
    take_profit_pct = safe_float(position_summary.get("take_profit_pct"))
    if take_profit_pct is None:
        take_profit_pct = safe_float(management_payload.get("take_profit_pct"))
    hours_to_expiration = safe_float(position_summary.get("hours_to_expiration"))
    exit_hours = safe_float(position_summary.get("exit_hours_to_expiration"))
    if exit_hours is None:
        exit_hours = safe_float(management_payload.get("exit_hours_to_expiration"))

    if (
        unrealized_pl_pct is not None
        and stop_loss_pct is not None
        and stop_loss_pct < 0
        and unrealized_pl_pct <= stop_loss_pct * 0.75
    ):
        return "Near stop loss"
    if (
        unrealized_pl_pct is not None
        and take_profit_pct is not None
        and take_profit_pct > 0
        and unrealized_pl_pct >= take_profit_pct * 0.75
    ):
        return "Near take profit"
    if (
        hours_to_expiration is not None
        and exit_hours is not None
        and hours_to_expiration <= exit_hours * 1.5
    ):
        return "Near expiration"

    return "Monitoring"


def _is_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _price_at_profit_pct(entry_price: float | None, profit_pct: float | None) -> float | None:
    if entry_price is None or profit_pct is None:
        return None
    return round(entry_price * (1.0 + (profit_pct / 100.0)), 4)


def build_option_protection_payload(
    *,
    snapshot: dict,
    management_payload: dict,
    entry_price: float | None,
) -> dict:
    """Return the manager's active price-exit floor and its recorded high-water mark.

    The manager persists trailing values as decimal P/L ratios (0.40 means 40%).
    The dashboard API exposes human-readable percentages and estimated option marks
    derived from the entry price, while making the source of the active floor explicit.
    """
    protected_floor_ratio = safe_float(snapshot.get("protected_profit_floor_pct"))
    protection_active = _is_enabled(snapshot.get("profit_protection_active"))
    stop_loss_pct = safe_float(snapshot.get("stop_loss_pct"))
    if stop_loss_pct is None:
        stop_loss_pct = safe_float(management_payload.get("stop_loss_pct"))

    if protection_active and protected_floor_ratio is not None:
        floor_pct = round(protected_floor_ratio * 100.0, 4)
        floor_type = "trailing_profit"
    elif stop_loss_pct is not None:
        floor_pct = stop_loss_pct
        floor_type = "stop_loss"
    else:
        floor_pct = None
        floor_type = "unavailable"

    high_water_ratio = safe_float(snapshot.get("max_pnl_pct"))
    high_water_pct = round(high_water_ratio * 100.0, 4) if high_water_ratio is not None else None

    return {
        "floor_type": floor_type,
        "floor_profit_pct": floor_pct,
        "floor_mark_price": _price_at_profit_pct(entry_price, floor_pct),
        "trailing_profit_armed": protection_active and protected_floor_ratio is not None,
        "high_water_profit_pct": high_water_pct,
        "high_water_mark_price": _price_at_profit_pct(entry_price, high_water_pct),
    }


def build_open_positions_payload() -> dict:
    live_positions = alpaca_get_json("/v2/positions")
    normalized_positions = live_positions if isinstance(live_positions, list) else []
    option_snapshots, management_payload = load_option_management_snapshot()
    thesis_verdicts = load_latest_thesis_verdicts()
    option_quote_cache: dict[str, dict] = {}

    rows: list[dict] = []
    option_count = 0
    stock_count = 0

    for raw_position in normalized_positions:
        if not isinstance(raw_position, dict):
            continue

        symbol = str(raw_position.get("symbol") or "").strip().upper()
        asset_class = str(raw_position.get("asset_class") or "")
        is_option = looks_like_option_symbol(symbol, asset_class)
        snapshot = option_snapshots.get(symbol, {})
        parsed_option = parse_option_symbol(symbol) if is_option else {}

        quantity = safe_float(snapshot.get("quantity")) if is_option else None
        if quantity is None:
            quantity = safe_float(raw_position.get("qty"))

        entry_price = safe_float(snapshot.get("entry_price")) if is_option else None
        if entry_price is None:
            entry_price = safe_float(raw_position.get("avg_entry_price"))

        current_bid = safe_float(snapshot.get("current_bid")) if is_option else None
        current_ask = safe_float(snapshot.get("current_ask")) if is_option else None
        if is_option and (current_bid is None or current_ask is None):
            latest_quote = get_latest_option_quote(symbol, option_quote_cache)
            if current_bid is None:
                current_bid = safe_float(latest_quote.get("bid_price"))
            if current_ask is None:
                current_ask = safe_float(latest_quote.get("ask_price"))
        mid_price = None
        if is_option and current_bid is not None and current_ask is not None:
            mid_price = round((current_bid + current_ask) / 2.0, 4)
        elif is_option:
            mid_price = safe_float(snapshot.get("mid_price"))
        if mid_price is None:
            mid_price = safe_float(raw_position.get("current_price"))

        mark_price = (
            resolve_option_mark_price(
                raw_position=raw_position,
                current_bid=current_bid,
                current_ask=current_ask,
                snapshot=snapshot,
            )
            if is_option
            else mid_price
        )

        unrealized_pl_pct = safe_float(snapshot.get("unrealized_pl_pct")) if is_option else None
        if unrealized_pl_pct is None:
            live_unrealized = safe_float(raw_position.get("unrealized_plpc"))
            unrealized_pl_pct = round(live_unrealized * 100.0, 4) if live_unrealized is not None else None
        if is_option:
            unrealized_pl_pct = compute_option_unrealized_pl_pct(
                entry_price,
                mark_price,
                unrealized_pl_pct,
            )

        expiration_text = str(snapshot.get("expiration_date") or parsed_option.get("expiration_date") or "")
        days_to_expiration = safe_float(snapshot.get("days_to_expiration"))
        if days_to_expiration is None and is_option:
            days_to_expiration = compute_days_to_expiration(expiration_text)

        if is_option:
            option_count += 1
            contract_type = str(snapshot.get("contract_type") or parsed_option.get("contract_type") or "").strip().lower()
            rows.append(
                {
                    "symbol": str(snapshot.get("underlying_symbol") or parsed_option.get("underlying_symbol") or symbol),
                    "contract_symbol": symbol,
                    "position_kind": "option",
                    "type": contract_type.title() if contract_type else "Option",
                    "strike": safe_float(snapshot.get("strike")) if snapshot else parsed_option.get("strike"),
                    "expiration": expiration_text,
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "current_bid": current_bid,
                    "current_ask": current_ask,
                    "mark_price": mark_price,
                    "mid_price": mid_price,
                    "unrealized_pl_pct": unrealized_pl_pct,
                    "days_to_expiration": days_to_expiration,
                    "profit_protection": build_option_protection_payload(
                        snapshot=snapshot,
                        management_payload=management_payload,
                        entry_price=entry_price,
                    ),
                    "exit_rule_status": format_exit_rule_status(snapshot, management_payload),
                    "decision": str(snapshot.get("decision") or ""),
                    "decision_reasons": snapshot.get("decision_reasons") if isinstance(snapshot.get("decision_reasons"), list) else [],
                    "thesis_verdict": thesis_verdicts.get(symbol),
                }
            )
            continue

        stock_count += 1
        rows.append(
            {
                "symbol": symbol,
                "contract_symbol": symbol,
                "position_kind": "stock",
                "type": "Stock",
                "strike": None,
                "expiration": "",
                "quantity": quantity,
                "entry_price": entry_price,
                "current_bid": None,
                "current_ask": None,
                "mark_price": mid_price,
                "mid_price": mid_price,
                "unrealized_pl_pct": unrealized_pl_pct,
                "days_to_expiration": None,
                "profit_protection": None,
                "exit_rule_status": "N/A",
                "decision": "",
                "decision_reasons": [],
                "thesis_verdict": None,
            }
        )

    rows.sort(
        key=lambda row: (
            0 if row.get("position_kind") == "option" else 1,
            str(row.get("expiration") or "9999-99-99"),
            str(row.get("symbol") or ""),
        )
    )

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "position_count": len(rows),
        "option_count": option_count,
        "stock_count": stock_count,
        "positions": rows,
    }
