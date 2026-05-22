from __future__ import annotations

from datetime import datetime, timezone
import re

from agent_pipeline.ranking import get_current_rankings
from portfolio_history_service import fetch_portfolio_history

from api_support.alpaca import alpaca_data_get_json, alpaca_get_json
from api_support.common import (
    load_dte_exit_rule_configs,
    parse_datetime,
    read_json_payload,
    safe_float,
)
from api_support.context import (
    AGENT_OUTPUT_PATH,
    BOT_DOWN_THRESHOLD_SECONDS,
    DEFAULT_OPTION_ORDER_QTY,
    MAX_DEPLOYABLE_BUYING_POWER_PCT,
    MAX_OPTION_ORDER_QTY_MULTIPLIER,
    OPTION_MANAGER_STATUS_PATH,
    OPTION_POSITION_MANAGEMENT_OUTPUT_PATH,
    OPTION_POSITION_SETTINGS,
    PER_ORDER_SIZING_BUYING_POWER_PCT,
    SCRIPT_STATUS_PATH,
)
from api_support.trades import build_trade_explanation_payload


def _build_empty_company_decisions_payload() -> dict:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "last_updated_at": "",
        "company_count": 0,
        "companies": [],
    }


def _normalize_company_identity(entry: dict) -> tuple[str, str]:
    company = entry.get("company")
    if not isinstance(company, dict):
        return "", ""
    symbol = str(company.get("symbol") or "").strip().upper()
    name = str(company.get("name") or "").strip()
    return symbol, name


def build_company_decisions_payload() -> dict:
    payload = read_json_payload(AGENT_OUTPUT_PATH)
    if not isinstance(payload, dict):
        return _build_empty_company_decisions_payload()

    strategist_results = payload.get("strategist")
    manager_results = payload.get("manager")
    if not isinstance(strategist_results, list):
        strategist_results = []
    if not isinstance(manager_results, list):
        manager_results = []

    last_updated_at = str(payload.get("ran_at") or "").strip()
    companies_by_symbol: dict[str, dict] = {}

    def get_or_create_company(symbol: str, name: str) -> dict:
        entry = companies_by_symbol.get(symbol)
        if entry is None:
            entry = {
                "symbol": symbol,
                "name": name,
                "last_updated_at": last_updated_at,
                "strategist": {
                    "decision": "",
                    "confidence": "",
                    "summary": "",
                    "thesis": [],
                    "risks": [],
                },
                "manager": {
                    "decision": "",
                    "confidence": "",
                    "reason": "",
                    "target_dte_bucket": "",
                    "selected_option_id": "",
                    "selected_expiration_date": "",
                    "selected_strike_price": None,
                    "selected_option_source": "",
                },
            }
            companies_by_symbol[symbol] = entry
        elif name and not entry.get("name"):
            entry["name"] = name
        return entry

    for raw_entry in strategist_results:
        if not isinstance(raw_entry, dict):
            continue
        symbol, name = _normalize_company_identity(raw_entry)
        if not symbol:
            continue
        recommendation = raw_entry.get("recommendation")
        if not isinstance(recommendation, dict):
            recommendation = {}
        company_entry = get_or_create_company(symbol, name)
        company_entry["strategist"] = {
            "decision": str(recommendation.get("decision") or "").strip(),
            "confidence": str(recommendation.get("confidence") or "").strip(),
            "summary": str(recommendation.get("summary") or "").strip(),
            "thesis": [
                str(item).strip()
                for item in recommendation.get("thesis", [])
                if str(item).strip()
            ]
            if isinstance(recommendation.get("thesis"), list)
            else [],
            "risks": [
                str(item).strip()
                for item in recommendation.get("risks", [])
                if str(item).strip()
            ]
            if isinstance(recommendation.get("risks"), list)
            else [],
        }

    for raw_entry in manager_results:
        if not isinstance(raw_entry, dict):
            continue
        symbol, name = _normalize_company_identity(raw_entry)
        if not symbol:
            continue
        recommendation = raw_entry.get("recommendation")
        if not isinstance(recommendation, dict):
            recommendation = {}
        company_entry = get_or_create_company(symbol, name)
        company_entry["manager"] = {
            "decision": str(recommendation.get("decision") or "").strip(),
            "confidence": str(recommendation.get("confidence") or "").strip(),
            "reason": str(recommendation.get("reason") or "").strip(),
            "target_dte_bucket": str(recommendation.get("target_dte_bucket") or "").strip(),
            "selected_option_id": str(recommendation.get("selected_option_id") or "").strip(),
            "selected_expiration_date": str(recommendation.get("selected_expiration_date") or "").strip(),
            "selected_strike_price": safe_float(recommendation.get("selected_strike_price")),
            "selected_option_source": str(recommendation.get("selected_option_source") or "").strip(),
        }

    companies = sorted(
        companies_by_symbol.values(),
        key=lambda entry: (str(entry.get("symbol") or ""), str(entry.get("name") or "")),
    )
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "last_updated_at": last_updated_at,
        "company_count": len(companies),
        "companies": companies,
    }


def _titleize_ranking_key(value: str) -> str:
    normalized = str(value or "").strip().replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in normalized.split())


def _build_top_rankings_payload() -> dict:
    try:
        rankings = get_current_rankings(top_sector_count=3, top_industry_count=3)
    except Exception:
        return {
            "top_sectors": [],
        }

    top_industries_by_sector = rankings.get("top_industries_by_sector", {})
    top_sectors = []
    for entry in rankings.get("top_sectors", []):
        if not isinstance(entry, dict):
            continue
        sector_key = str(entry.get("sector_key") or "").strip()
        if not sector_key:
            continue
        ranked_industries = []
        if isinstance(top_industries_by_sector, dict):
            for industry_entry in top_industries_by_sector.get(sector_key, []):
                if not isinstance(industry_entry, dict):
                    continue
                industry_key = str(industry_entry.get("industry_key") or "").strip()
                if not industry_key:
                    continue
                ranked_industries.append(
                    {
                        "industry_key": industry_key,
                        "label": _titleize_ranking_key(industry_key),
                        "score": safe_float(industry_entry.get("score")),
                    }
                )
        top_sectors.append(
            {
                "sector_key": sector_key,
                "label": _titleize_ranking_key(sector_key),
                "score": safe_float(entry.get("score")),
                "top_industries": ranked_industries[:3],
            }
        )

    return {
        "top_sectors": top_sectors[:3],
    }


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


def compute_max_drawdown_pct(portfolio_history: dict) -> float | None:
    equity_series = portfolio_history.get("equity")
    if not isinstance(equity_series, list) or not equity_series:
        return None

    peak = None
    max_drawdown_pct = 0.0
    for raw_value in equity_series:
        value = safe_float(raw_value)
        if value is None:
            continue
        if peak is None or value > peak:
            peak = value
            continue
        if peak and peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, ((peak - value) / peak) * 100.0)

    return round(max_drawdown_pct, 2)


def compute_days_to_expiration(expiration_date_text: str | None) -> float | None:
    if not expiration_date_text:
        return None

    parsed_expiration = parse_datetime(str(expiration_date_text))
    if parsed_expiration is None:
        return None

    expiration_date = parsed_expiration.date()
    current_date = datetime.now(timezone.utc).date()
    return float((expiration_date - current_date).days)


def compute_win_rate_from_fills(fills) -> dict:
    if not isinstance(fills, list):
        return {"wins": 0, "closed_trades": 0, "win_rate_pct": None}

    ordered_fills = sorted(
        fills,
        key=lambda fill: parse_datetime(str(fill.get("transaction_time") or "")) or datetime.min.replace(tzinfo=timezone.utc),
    )

    inventory: dict[str, dict[str, float]] = {}
    wins = 0
    closed_trades = 0

    for fill in ordered_fills:
        symbol = str(fill.get("symbol") or "").strip().upper()
        side = str(fill.get("side") or fill.get("order_side") or "").strip().lower()
        qty = safe_float(fill.get("qty"))
        price = safe_float(fill.get("price"))
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


def summarize_market_status(clock_payload: dict) -> dict:
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


def status_snapshot(name: str, payload) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {"name": name, "state": "down", "label": "Down", "updated_at": ""}

    updated_at = str(payload.get("updated_at") or "")
    updated_at_dt = parse_datetime(updated_at)
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


def summarize_bot_status(worker_status, option_manager_status) -> dict:
    components = []
    worker_snapshot = status_snapshot("Worker", worker_status)
    if worker_snapshot.get("updated_at") or worker_snapshot.get("state") != "down":
        components.append(worker_snapshot)

    option_snapshot = status_snapshot("Option Manager", option_manager_status)
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


def build_open_positions_payload() -> dict:
    live_positions = alpaca_get_json("/v2/positions")
    normalized_positions = live_positions if isinstance(live_positions, list) else []
    option_snapshots, management_payload = load_option_management_snapshot()
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
                    "exit_rule_status": format_exit_rule_status(snapshot, management_payload),
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
                "mark_price": mid_price,
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


def build_risk_controls_payload() -> dict:
    fallback_take_profit_pct = OPTION_POSITION_SETTINGS.take_profit_pct
    fallback_stop_loss_pct = OPTION_POSITION_SETTINGS.stop_loss_pct
    expiration_exit_hours = OPTION_POSITION_SETTINGS.exit_hours_to_expiration
    dte_rule_configs = load_dte_exit_rule_configs()
    take_profit_rule_summary = ", ".join(
        f"{rule['label']}: +{rule['take_profit_pct']:.0f}%"
        for rule in dte_rule_configs
    )
    stop_loss_rule_summary = ", ".join(
        f"{rule['label']}: {rule['stop_loss_pct']:.0f}%"
        for rule in dte_rule_configs
    )
    expiration_rule_summary = ", ".join(
        f"{rule['label']}: <={rule['force_exit_days_to_expiration']} DTE"
        for rule in dte_rule_configs
    )
    fallback_threshold_detail = (
        "Positions outside those DTE buckets fall back to the legacy defaults "
        f"({fallback_take_profit_pct:.0f}% TP / {fallback_stop_loss_pct:.0f}% SL / {expiration_exit_hours:.0f}h expiration exit)."
        if fallback_take_profit_pct is not None
        and fallback_stop_loss_pct is not None
        and expiration_exit_hours is not None
        else "Positions outside those DTE buckets fall back to the legacy default thresholds when configured."
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
                "value": stop_loss_rule_summary,
                "detail": (
                    "Open option positions use DTE-specific stop-loss floors. "
                    + fallback_threshold_detail
                ),
                "status": "configured",
            },
            {
                "label": "Take-profit rule",
                "value": take_profit_rule_summary,
                "detail": (
                    "Open option positions use DTE-specific take-profit targets. "
                    + fallback_threshold_detail
                ),
                "status": "configured",
            },
            {
                "label": "Expiration exit rule",
                "value": expiration_rule_summary,
                "detail": (
                    "The option manager uses DTE-specific forced exits before expiration. "
                    + fallback_threshold_detail
                ),
                "status": "configured",
            },
        ],
    }


def build_dashboard_kpis() -> dict:
    account = alpaca_get_json("/v2/account")
    positions = alpaca_get_json("/v2/positions")
    clock = alpaca_get_json("/v2/clock")
    portfolio_history = fetch_portfolio_history()

    try:
        fills = alpaca_get_json(
            "/v2/account/activities/FILL",
            {"direction": "desc", "page_size": "100"},
        )
    except Exception:
        fills = []

    equity = safe_float(account.get("equity"))
    buying_power = safe_float(account.get("buying_power"))
    last_equity = safe_float(account.get("last_equity"))
    day_pl = None
    day_pl_pct = None
    if equity is not None and last_equity not in (None, 0):
        day_pl = round(equity - last_equity, 2)
        day_pl_pct = round((day_pl / last_equity) * 100.0, 2)

    normalized_positions = positions if isinstance(positions, list) else []
    option_positions = [
        position
        for position in normalized_positions
        if looks_like_option_symbol(
            str(position.get("symbol") or ""),
            str(position.get("asset_class") or ""),
        )
    ]
    option_exposure = sum(
        abs(safe_float(position.get("market_value")) or 0.0)
        for position in option_positions
    )
    option_exposure_pct = round((option_exposure / equity) * 100.0, 2) if equity not in (None, 0) else None
    dte_rule_configs = load_dte_exit_rule_configs()
    win_rate = compute_win_rate_from_fills(fills)
    worker_status = read_json_payload(SCRIPT_STATUS_PATH)
    option_manager_status = read_json_payload(OPTION_MANAGER_STATUS_PATH)
    top_rankings = _build_top_rankings_payload()

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
        "option_exit_rules": {
            "take_profit_summary": " / ".join(
                f"{rule['label']}: +{rule['take_profit_pct']:.0f}%"
                for rule in dte_rule_configs
            ),
            "stop_loss_summary": " / ".join(
                f"{rule['label']}: {rule['stop_loss_pct']:.0f}%"
                for rule in dte_rule_configs
            ),
            "fallback_take_profit_pct": OPTION_POSITION_SETTINGS.take_profit_pct,
            "fallback_stop_loss_pct": OPTION_POSITION_SETTINGS.stop_loss_pct,
        },
        "win_rate": win_rate,
        "max_drawdown_pct": compute_max_drawdown_pct(portfolio_history),
        "bot_status": summarize_bot_status(worker_status, option_manager_status),
        "market_status": summarize_market_status(clock),
        "top_rankings": top_rankings,
    }
