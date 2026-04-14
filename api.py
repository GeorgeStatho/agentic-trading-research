from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify

from portfolio_history_service import env_flag, fetch_portfolio_history, load_env


load_env()
app = Flask(__name__)
ROOT_DIR = Path(__file__).resolve().parent
SCRIPT_STATUS_PATH = Path(
    os.getenv("SCRIPT_STATUS_PATH", str(ROOT_DIR / "web_dashboard" / "public" / "script_status.json"))
)
TRADE_EXECUTION_OUTPUT_PATH = Path(
    os.getenv(
        "TRADE_EXECUTION_OUTPUT_PATH",
        str(ROOT_DIR / "Data" / "trade_execution_output.json"),
    )
)
SELECTED_OPTIONS_OUTPUT_PATH = ROOT_DIR / "Data" / "selected_options_output.json"
OPTION_POSITION_MANAGEMENT_OUTPUT_PATH = Path(
    os.getenv(
        "OPTION_POSITION_MANAGEMENT_OUTPUT_PATH",
        str(ROOT_DIR / "Data" / "option_position_management_output.json"),
    )
)
OPTION_MANAGER_STATUS_PATH = Path(
    os.getenv(
        "OPTION_MANAGER_STATUS_PATH",
        str(ROOT_DIR / "Data" / "option_manager_status.json"),
    )
)
BOT_DOWN_THRESHOLD_SECONDS = max(
    30,
    int(os.getenv("BOT_STATUS_DOWN_THRESHOLD_SECONDS", "90")),
)
DEFAULT_OPTION_ORDER_QTY = max(1, int(os.getenv("AGENT_OPTION_ORDER_QTY", "1")))


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


@app.get("/api/health")
def health() -> tuple[dict, int]:
    return {"status": "ok"}, 200


@app.get("/api/portfolio-history")
def portfolio_history():
    try:
        payload = fetch_portfolio_history()
        return jsonify(payload), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _safe_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


MAX_DEPLOYABLE_BUYING_POWER_PCT = min(
    100.0,
    max(0.0, _safe_float(os.getenv("MAX_DEPLOYABLE_BUYING_POWER_PCT")) or 30.0),
)
PER_ORDER_SIZING_BUYING_POWER_PCT = min(
    100.0,
    max(0.0, _safe_float(os.getenv("PER_ORDER_SIZING_BUYING_POWER_PCT")) or 30.0),
)
MAX_OPTION_ORDER_QTY_MULTIPLIER = max(
    1,
    int(_safe_float(os.getenv("MAX_OPTION_ORDER_QTY_MULTIPLIER")) or 50),
)


def _alpaca_base_url() -> str:
    return (
        "https://paper-api.alpaca.markets"
        if env_flag("ALPACA_PAPER", True)
        else "https://api.alpaca.markets"
    )


def _alpaca_data_base_url() -> str:
    return "https://data.alpaca.markets"


def _alpaca_get_json(path: str, query: dict[str, str] | None = None):
    api_key = str(os.getenv("PUBLIC_KEY") or "").strip()
    api_secret = str(os.getenv("PRIVATE_KEY") or "").strip()

    if not api_key or not api_secret:
        raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in Stock-trading-experiment/.env")

    url = f"{_alpaca_base_url()}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"

    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request) as response:
            return json.load(response)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca request failed with status {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach Alpaca API: {exc.reason}") from exc


def _alpaca_data_get_json(path: str, query: dict[str, str] | None = None):
    api_key = str(os.getenv("PUBLIC_KEY") or "").strip()
    api_secret = str(os.getenv("PRIVATE_KEY") or "").strip()

    if not api_key or not api_secret:
        raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in Stock-trading-experiment/.env")

    url = f"{_alpaca_data_base_url()}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"

    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request) as response:
            return json.load(response)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca market-data request failed with status {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach Alpaca market-data API: {exc.reason}") from exc


def _read_json_payload(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _parse_datetime(value: str | None):
    if not value:
        return None

    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _status_snapshot(name: str, payload) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {"name": name, "state": "down", "label": "Down", "updated_at": ""}

    updated_at = str(payload.get("updated_at") or "")
    updated_at_dt = _parse_datetime(updated_at)
    is_fresh = False
    if updated_at_dt is not None:
        is_fresh = (
            datetime.now(timezone.utc) - updated_at_dt.astimezone(timezone.utc)
        ).total_seconds() <= BOT_DOWN_THRESHOLD_SECONDS

    raw_state = str(payload.get("state") or "").strip().lower()
    state = raw_state if raw_state in {"starting", "running", "paused", "error"} else "unknown"
    if not is_fresh:
        state = "down"

    label = state.replace("_", " ").title()
    return {
        "name": name,
        "state": state,
        "label": label,
        "updated_at": updated_at,
    }


def _summarize_bot_status(worker_status, option_manager_status) -> dict:
    components = []
    worker_snapshot = _status_snapshot("Worker", worker_status)
    if worker_snapshot.get("updated_at") or worker_snapshot.get("state") != "down":
        components.append(worker_snapshot)

    option_snapshot = _status_snapshot("Option Manager", option_manager_status)
    if option_snapshot.get("updated_at") or option_snapshot.get("state") != "down":
        components.append(option_snapshot)

    if not components:
        components = [worker_snapshot]

    states = {component["state"] for component in components}
    if states == {"running"}:
        overall_state = "healthy"
        label = "Healthy"
    elif states & {"running", "paused", "starting"}:
        overall_state = "degraded"
        label = "Degraded"
    else:
        overall_state = "down"
        label = "Down"

    detail = ", ".join(
        f"{component['name']} {component['label'].lower()}" for component in components
    )
    return {
        "state": overall_state,
        "label": label,
        "detail": detail,
        "components": components,
    }


def _looks_like_option_symbol(symbol: str, asset_class: str = "") -> bool:
    normalized_asset_class = str(asset_class or "").strip().lower()
    if "option" in normalized_asset_class:
        return True
    return bool(re.fullmatch(r"[A-Z]+\d{6}[CP]\d{8}", str(symbol or "").strip().upper()))


def _parse_option_symbol(symbol: str) -> dict[str, object]:
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


def _compute_max_drawdown_pct(portfolio_history: dict) -> float | None:
    equity_series = portfolio_history.get("equity")
    if not isinstance(equity_series, list) or not equity_series:
        return None

    peak = None
    max_drawdown_pct = 0.0
    for raw_value in equity_series:
        value = _safe_float(raw_value)
        if value is None:
            continue
        if peak is None or value > peak:
            peak = value
            continue
        if peak and peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, ((peak - value) / peak) * 100.0)

    return round(max_drawdown_pct, 2)


def _compute_days_to_expiration(expiration_date_text: str | None) -> float | None:
    if not expiration_date_text:
        return None

    parsed_expiration = _parse_datetime(str(expiration_date_text))
    if parsed_expiration is None:
        return None

    expiration_date = parsed_expiration.date()
    current_date = datetime.now(timezone.utc).date()
    return float((expiration_date - current_date).days)


def _compute_win_rate_from_fills(fills) -> dict:
    if not isinstance(fills, list):
        return {"wins": 0, "closed_trades": 0, "win_rate_pct": None}

    ordered_fills = sorted(
        fills,
        key=lambda fill: _parse_datetime(str(fill.get("transaction_time") or "")) or datetime.min.replace(tzinfo=timezone.utc),
    )

    inventory: dict[str, dict[str, float]] = {}
    wins = 0
    closed_trades = 0

    for fill in ordered_fills:
        symbol = str(fill.get("symbol") or "").strip().upper()
        side = str(fill.get("side") or fill.get("order_side") or "").strip().lower()
        qty = _safe_float(fill.get("qty"))
        price = _safe_float(fill.get("price"))
        if not symbol or side not in {"buy", "sell"} or qty is None or qty <= 0 or price is None:
            continue

        position = inventory.setdefault(symbol, {"qty": 0.0, "avg_cost": 0.0})
        if side == "buy":
            total_cost = (position["qty"] * position["avg_cost"]) + (qty * price)
            position["qty"] += qty
            if position["qty"] > 0:
                position["avg_cost"] = total_cost / position["qty"]
            continue

        if position["qty"] <= 0:
            continue

        closed_qty = min(position["qty"], qty)
        realized_pl = (price - position["avg_cost"]) * closed_qty
        closed_trades += 1
        if realized_pl > 0:
            wins += 1

        position["qty"] = max(0.0, position["qty"] - qty)
        if position["qty"] == 0:
            position["avg_cost"] = 0.0

    win_rate_pct = round((wins / closed_trades) * 100.0, 1) if closed_trades else None
    return {
        "wins": wins,
        "closed_trades": closed_trades,
        "win_rate_pct": win_rate_pct,
    }


def _summarize_market_status(clock_payload: dict) -> dict:
    is_open = bool(clock_payload.get("is_open", False))
    next_open = str(clock_payload.get("next_open") or "")
    next_close = str(clock_payload.get("next_close") or "")
    timestamp = str(clock_payload.get("timestamp") or "")
    return {
        "state": "open" if is_open else "closed",
        "label": "Open" if is_open else "Closed",
        "detail": f"Next close: {next_close}" if is_open and next_close else f"Next open: {next_open}" if next_open else "",
        "timestamp": timestamp,
        "next_open": next_open,
        "next_close": next_close,
    }


def _load_option_management_snapshot() -> tuple[dict[str, dict], dict]:
    payload = _read_json_payload(OPTION_POSITION_MANAGEMENT_OUTPUT_PATH)
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


def _extract_option_quote_fields(quote_payload: dict) -> dict[str, float | str | None]:
    if not isinstance(quote_payload, dict):
        return {"bid_price": None, "ask_price": None, "timestamp": ""}

    bid_price = _safe_float(
        quote_payload.get("bid_price")
        or quote_payload.get("bp")
    )
    ask_price = _safe_float(
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


def _get_latest_option_quote(symbol: str, quote_cache: dict[str, dict]) -> dict[str, float | str | None]:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return {"bid_price": None, "ask_price": None, "timestamp": ""}

    if normalized_symbol in quote_cache:
        return quote_cache[normalized_symbol]

    fallback = {"bid_price": None, "ask_price": None, "timestamp": ""}
    try:
        payload = _alpaca_data_get_json(
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
    extracted = _extract_option_quote_fields(quote)
    quote_cache[normalized_symbol] = extracted
    return extracted


def _format_exit_rule_status(position_summary: dict, management_payload: dict) -> str:
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

    unrealized_pl_pct = _safe_float(position_summary.get("unrealized_pl_pct"))
    stop_loss_pct = _safe_float(management_payload.get("stop_loss_pct"))
    take_profit_pct = _safe_float(management_payload.get("take_profit_pct"))
    hours_to_expiration = _safe_float(position_summary.get("hours_to_expiration"))
    exit_hours = _safe_float(management_payload.get("exit_hours_to_expiration"))

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


def _build_open_positions_payload() -> dict:
    live_positions = _alpaca_get_json("/v2/positions")
    normalized_positions = live_positions if isinstance(live_positions, list) else []
    option_snapshots, management_payload = _load_option_management_snapshot()
    option_quote_cache: dict[str, dict] = {}

    rows: list[dict] = []
    option_count = 0
    stock_count = 0

    for raw_position in normalized_positions:
        if not isinstance(raw_position, dict):
            continue

        symbol = str(raw_position.get("symbol") or "").strip().upper()
        asset_class = str(raw_position.get("asset_class") or "")
        is_option = _looks_like_option_symbol(symbol, asset_class)
        snapshot = option_snapshots.get(symbol, {})
        parsed_option = _parse_option_symbol(symbol) if is_option else {}

        quantity = _safe_float(snapshot.get("quantity")) if is_option else None
        if quantity is None:
            quantity = _safe_float(raw_position.get("qty"))

        entry_price = _safe_float(snapshot.get("entry_price")) if is_option else None
        if entry_price is None:
            entry_price = _safe_float(raw_position.get("avg_entry_price"))

        current_bid = _safe_float(snapshot.get("current_bid")) if is_option else None
        current_ask = _safe_float(snapshot.get("current_ask")) if is_option else None
        if is_option and (current_bid is None or current_ask is None):
            latest_quote = _get_latest_option_quote(symbol, option_quote_cache)
            if current_bid is None:
                current_bid = _safe_float(latest_quote.get("bid_price"))
            if current_ask is None:
                current_ask = _safe_float(latest_quote.get("ask_price"))
        mid_price = _safe_float(snapshot.get("mid_price")) if is_option else None
        if mid_price is None and current_bid is not None and current_ask is not None:
            mid_price = round((current_bid + current_ask) / 2.0, 4)
        if mid_price is None:
            mid_price = _safe_float(raw_position.get("current_price"))

        unrealized_pl_pct = _safe_float(snapshot.get("unrealized_pl_pct")) if is_option else None
        if unrealized_pl_pct is None:
            live_unrealized = _safe_float(raw_position.get("unrealized_plpc"))
            unrealized_pl_pct = round(live_unrealized * 100.0, 4) if live_unrealized is not None else None

        expiration_text = str(snapshot.get("expiration_date") or parsed_option.get("expiration_date") or "")
        days_to_expiration = _safe_float(snapshot.get("days_to_expiration"))
        if days_to_expiration is None and is_option:
            days_to_expiration = _compute_days_to_expiration(expiration_text)

        if is_option:
            option_count += 1
            contract_type = str(snapshot.get("contract_type") or parsed_option.get("contract_type") or "").strip().lower()
            rows.append(
                {
                    "symbol": str(snapshot.get("underlying_symbol") or parsed_option.get("underlying_symbol") or symbol),
                    "contract_symbol": symbol,
                    "position_kind": "option",
                    "type": contract_type.title() if contract_type else "Option",
                    "strike": _safe_float(snapshot.get("strike")) if snapshot else parsed_option.get("strike"),
                    "expiration": expiration_text,
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "current_bid": current_bid,
                    "current_ask": current_ask,
                    "mid_price": mid_price,
                    "unrealized_pl_pct": unrealized_pl_pct,
                    "days_to_expiration": days_to_expiration,
                    "exit_rule_status": _format_exit_rule_status(snapshot, management_payload),
                    "decision": str(snapshot.get("decision") or ""),
                    "decision_reasons": snapshot.get("decision_reasons") if isinstance(snapshot.get("decision_reasons"), list) else [],
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
                "mid_price": mid_price,
                "unrealized_pl_pct": unrealized_pl_pct,
                "days_to_expiration": None,
                "exit_rule_status": "N/A",
                "decision": "",
                "decision_reasons": [],
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


def _load_output_payload(path: Path) -> dict:
    payload = _read_json_payload(path)
    return payload if isinstance(payload, dict) else {}


def _humanize_rejection_reason(error_value: object) -> str:
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


def _format_decision_label(decision: str) -> str:
    normalized = str(decision or "").strip().lower()
    if normalized == "call":
        return "Buy Call"
    if normalized == "put":
        return "Buy Put"
    if normalized in {"neither", "do_not_trade"}:
        return "No Trade"
    return normalized.replace("_", " ").title() if normalized else "No Trade"


def _format_selected_contract(execution: dict) -> str:
    selected_option = execution.get("selected_option")
    if not isinstance(selected_option, dict):
        return ""

    contract_type = str(
        selected_option.get("contract_type")
        or execution.get("decision")
        or ""
    ).strip().lower()
    contract_letter = "C" if contract_type == "call" else "P" if contract_type == "put" else ""
    strike = _safe_float(
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


def _build_trade_explanation_payload() -> dict:
    trade_output = _load_output_payload(TRADE_EXECUTION_OUTPUT_PATH)
    selected_output = _load_output_payload(SELECTED_OPTIONS_OUTPUT_PATH)

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

    rejected_because = _humanize_rejection_reason(chosen_execution.get("error"))
    return {
        "ran_at": str(trade_output.get("ran_at") or selected_output.get("ran_at") or ""),
        "has_decision": True,
        "ticker": str(chosen_execution.get("symbol") or "").strip().upper(),
        "decision": _format_decision_label(str(chosen_execution.get("decision") or "")),
        "confidence": str(chosen_execution.get("confidence") or "").strip().lower(),
        "reason": str(chosen_execution.get("reason") or "").strip(),
        "selected_contract": _format_selected_contract(chosen_execution),
        "rejected_because": rejected_because,
        "submitted": bool(chosen_execution.get("submitted")),
    }


def _build_risk_controls_payload() -> dict:
    stop_loss_pct = _safe_float(os.getenv("OPTION_POSITION_STOP_LOSS_PCT"))
    take_profit_pct = _safe_float(os.getenv("OPTION_POSITION_TAKE_PROFIT_PCT"))
    expiration_exit_hours = _safe_float(
        os.getenv(
            "OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION",
            os.getenv("OPTION_POSITION_EXIT_DAYS_TO_EXPIRATION"),
        )
    )

    per_trade_buying_power_cap_pct = PER_ORDER_SIZING_BUYING_POWER_PCT
    total_options_exposure_cap_pct = MAX_DEPLOYABLE_BUYING_POWER_PCT
    effective_per_trade_cap_pct = round(
        (MAX_DEPLOYABLE_BUYING_POWER_PCT * PER_ORDER_SIZING_BUYING_POWER_PCT) / 100.0,
        2,
    )

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "controls": [
            {
                "label": "Max risk per trade",
                "value": (
                    f"{per_trade_buying_power_cap_pct:.0f}% of deployable buying power "
                    f"(~{effective_per_trade_cap_pct:.2f}% of total buying power)"
                ),
                "detail": (
                    "Current order sizing uses the configured share of deployable buying power for a single option idea, "
                    "subject to contract-price math and the AGENT_OPTION_ORDER_QTY baseline."
                ),
                "status": "configured",
            },
            {
                "label": "Max open contracts",
                "value": f"Base qty {DEFAULT_OPTION_ORDER_QTY}, up to x{MAX_OPTION_ORDER_QTY_MULTIPLIER} per idea",
                "detail": (
                    "There is still no portfolio-wide cap on total open contracts, but a single idea's order size is bounded "
                    "by the configured multiplier on top of the AGENT_OPTION_ORDER_QTY baseline."
                ),
                "status": "configured",
            },
            {
                "label": "Max total options exposure",
                "value": f"{total_options_exposure_cap_pct:.0f}% of account buying power",
                "detail": (
                    "The execution loop stops adding new option orders once the configured "
                    "deployable buying power allowance is consumed."
                ),
                "status": "configured",
            },
            {
                "label": "Stop-loss rule",
                "value": f"{stop_loss_pct:.0f}% P/L" if stop_loss_pct is not None else "Not configured",
                "detail": "Open option positions are flagged for exit once unrealized P/L falls through the configured stop-loss floor.",
                "status": "configured" if stop_loss_pct is not None else "missing",
            },
            {
                "label": "Take-profit rule",
                "value": f"{take_profit_pct:.0f}% P/L" if take_profit_pct is not None else "Not configured",
                "detail": "Open option positions are flagged for exit once unrealized P/L reaches the configured take-profit target.",
                "status": "configured" if take_profit_pct is not None else "missing",
            },
            {
                "label": "Expiration exit rule",
                "value": (
                    f"{expiration_exit_hours:.0f} hours to expiration"
                    if expiration_exit_hours is not None
                    else "Not configured"
                ),
                "detail": "The option manager exits contracts once they are at or inside the configured hours-to-expiration threshold.",
                "status": "configured" if expiration_exit_hours is not None else "missing",
            },
        ],
    }


def _build_dashboard_kpis() -> dict:
    account = _alpaca_get_json("/v2/account")
    positions = _alpaca_get_json("/v2/positions")
    clock = _alpaca_get_json("/v2/clock")
    portfolio_history = fetch_portfolio_history()

    try:
        fills = _alpaca_get_json(
            "/v2/account/activities/FILL",
            {"direction": "desc", "page_size": "100"},
        )
    except Exception:
        fills = []

    equity = _safe_float(account.get("equity"))
    buying_power = _safe_float(account.get("buying_power"))
    last_equity = _safe_float(account.get("last_equity"))
    day_pl = None
    day_pl_pct = None
    if equity is not None and last_equity not in (None, 0):
        day_pl = round(equity - last_equity, 2)
        day_pl_pct = round((day_pl / last_equity) * 100.0, 2)

    normalized_positions = positions if isinstance(positions, list) else []
    option_positions = [
        position
        for position in normalized_positions
        if _looks_like_option_symbol(
            str(position.get("symbol") or ""),
            str(position.get("asset_class") or ""),
        )
    ]
    option_exposure = sum(
        abs(_safe_float(position.get("market_value")) or 0.0)
        for position in option_positions
    )
    option_exposure_pct = round((option_exposure / equity) * 100.0, 2) if equity not in (None, 0) else None
    win_rate = _compute_win_rate_from_fills(fills)
    worker_status = _read_json_payload(SCRIPT_STATUS_PATH)
    option_manager_status = _read_json_payload(OPTION_MANAGER_STATUS_PATH)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "account_equity": equity,
        "buying_power": buying_power,
        "day_pl": day_pl,
        "day_pl_pct": day_pl_pct,
        "open_positions": len(normalized_positions),
        "options_exposure": {
            "market_value": round(option_exposure, 2),
            "equity_pct": option_exposure_pct,
            "position_count": len(option_positions),
        },
        "win_rate": win_rate,
        "max_drawdown_pct": _compute_max_drawdown_pct(portfolio_history),
        "bot_status": _summarize_bot_status(worker_status, option_manager_status),
        "market_status": _summarize_market_status(clock),
    }


def read_json_file(path: Path) -> tuple[dict, int]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return jsonify(json.load(handle)), 200
    except FileNotFoundError:
        return jsonify({"error": f"File not found: {path.name}"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/script-status")
def script_status():
    return read_json_file(SCRIPT_STATUS_PATH)


@app.get("/api/trade-execution-output")
def trade_execution_output():
    return read_json_file(TRADE_EXECUTION_OUTPUT_PATH)


@app.get("/api/dashboard-kpis")
def dashboard_kpis():
    try:
        return jsonify(_build_dashboard_kpis()), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/open-positions")
def open_positions():
    try:
        return jsonify(_build_open_positions_payload()), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/why-bot-traded")
def why_bot_traded():
    try:
        return jsonify(_build_trade_explanation_payload()), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/risk-controls")
def risk_controls():
    try:
        return jsonify(_build_risk_controls_payload()), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
