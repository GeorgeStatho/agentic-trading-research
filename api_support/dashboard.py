from __future__ import annotations

from datetime import datetime, timezone

from agent_pipeline.ranking import get_current_rankings
from market_db_modules.telemetry import get_telemetry_summary
from performance_metrics import (
    DEFAULT_ANNUAL_RISK_FREE_RATE,
    compute_daily_returns,
    compute_sharpe_metrics,
    compute_sortino_metrics,
    compute_total_return_pct,
    compute_trade_metrics,
    normalize_equity_curve,
)
from portfolio_history_service import fetch_portfolio_history

from api_support.alpaca import alpaca_get_json
from api_support.request_utils import load_dte_exit_rule_configs
from services.core.numbers import safe_float
from api_support.context import (
    DEFAULT_OPTION_ORDER_QTY,
    DAILY_OPTION_LOSS_LIMIT_DOLLARS,
    MAX_CONCURRENT_OPTION_POSITIONS,
    MAX_OPTION_ENTRIES_PER_SYMBOL_PER_WINDOW,
    MAX_PREMIUM_PER_TRADE_DOLLARS,
    MAX_PREMIUM_PER_TRADE_PCT,
    OPTION_SYMBOL_ENTRY_WINDOW_DAYS,
    OPTION_SYMBOL_STOP_LOSS_COOLDOWN_DAYS,
    MAX_DEPLOYABLE_BUYING_POWER_PCT,
    MAX_OPTION_ORDER_QTY_MULTIPLIER,
    MAX_OPTION_SECTOR_EXPOSURE_PCT,
    OPTION_POSITION_SETTINGS,
    PER_ORDER_SIZING_BUYING_POWER_PCT,
)
from api_support.context import logger
import os
from services.core.time_utils import parse_timestamp
from api_support.bot_status import read_bot_components, summarize_bot_status, summarize_market_status
from api_support.company_decisions import build_market_regime_payload
from api_support.decision_expectancy import titleize_key
from api_support.open_positions import looks_like_option_symbol


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
                        "label": titleize_key(industry_key),
                        "score": safe_float(industry_entry.get("score")),
                    }
                )
        top_sectors.append(
            {
                "sector_key": sector_key,
                "label": titleize_key(sector_key),
                "score": safe_float(entry.get("score")),
                "top_industries": ranked_industries[:3],
            }
        )

    return {
        "top_sectors": top_sectors[:3],
    }


def _build_usage_summary() -> dict:
    try:
        summary = get_telemetry_summary(project="Stock-trading-experiment")
    except Exception:
        return {
            "unique_install_count": 0,
            "startup_ping_count": 0,
            "last_seen_at": "",
        }

    return {
        "unique_install_count": int(summary.get("unique_install_count") or 0),
        "startup_ping_count": int(summary.get("startup_ping_count") or 0),
        "last_seen_at": str(summary.get("last_seen_at") or "").strip(),
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


def compute_win_rate_from_fills(fills) -> dict:
    if not isinstance(fills, list):
        return {"wins": 0, "closed_trades": 0, "win_rate_pct": None}

    ordered_fills = sorted(
        fills,
        key=lambda fill: parse_timestamp(str(fill.get("transaction_time") or "")) or datetime.min.replace(tzinfo=timezone.utc),
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
                "label": "Premium cap per trade",
                "value": (
                    " or ".join(
                        limit
                        for limit in (
                            f"{MAX_PREMIUM_PER_TRADE_PCT:.0f}% of account buying power"
                            if MAX_PREMIUM_PER_TRADE_PCT > 0
                            else "",
                            f"${MAX_PREMIUM_PER_TRADE_DOLLARS:,.0f}"
                            if MAX_PREMIUM_PER_TRADE_DOLLARS > 0
                            else "",
                        )
                        if limit
                    )
                    + ", whichever is tighter"
                    if MAX_PREMIUM_PER_TRADE_PCT > 0 or MAX_PREMIUM_PER_TRADE_DOLLARS > 0
                    else "disabled"
                ),
                "detail": (
                    "Hard ceiling on the premium of one new entry. The order is shrunk to fit, and rejected "
                    "outright when a single contract already costs more than the allowance."
                ),
                "status": (
                    "configured"
                    if MAX_PREMIUM_PER_TRADE_PCT > 0 or MAX_PREMIUM_PER_TRADE_DOLLARS > 0
                    else "disabled"
                ),
            },
            {
                "label": "Per-symbol entry limits",
                "value": (
                    f"{MAX_OPTION_ENTRIES_PER_SYMBOL_PER_WINDOW} entries per {OPTION_SYMBOL_ENTRY_WINDOW_DAYS}d"
                    if MAX_OPTION_ENTRIES_PER_SYMBOL_PER_WINDOW > 0
                    else "no attempt cap"
                )
                + (
                    f"; {OPTION_SYMBOL_STOP_LOSS_COOLDOWN_DAYS}d cooldown after a stop-loss exit"
                    if OPTION_SYMBOL_STOP_LOSS_COOLDOWN_DAYS > 0
                    else ""
                ),
                "detail": (
                    "New entries in one underlying are rejected once the rolling attempt cap is reached, "
                    "or while the post-stop-loss cooldown for that underlying is still active."
                ),
                "status": (
                    "configured"
                    if MAX_OPTION_ENTRIES_PER_SYMBOL_PER_WINDOW > 0 or OPTION_SYMBOL_STOP_LOSS_COOLDOWN_DAYS > 0
                    else "disabled"
                ),
            },
            {
                "label": "Max open contracts",
                "value": f"{MAX_CONCURRENT_OPTION_POSITIONS} open positions; base qty {DEFAULT_OPTION_ORDER_QTY}, up to x{MAX_OPTION_ORDER_QTY_MULTIPLIER} per idea",
                "detail": (
                    "New entries are rejected once the broker reports the configured portfolio-wide option-position count. "
                    "Each accepted idea remains subject to the configured per-idea quantity multiplier."
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
                "label": "Sector concentration cap",
                "value": f"{MAX_OPTION_SECTOR_EXPOSURE_PCT:.0f}% of deployable option buying power",
                "detail": "New entries are rejected when their persisted company sector would exceed this share of the total option-notional cap.",
                "status": "configured",
            },
            {
                "label": "Daily realized-loss limit",
                "value": f"${DAILY_OPTION_LOSS_LIMIT_DOLLARS:,.0f}",
                "detail": "New entries are rejected after persisted broker-confirmed option losses for the current market day reach this limit.",
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


def _build_thesis_validator_kpi_payload() -> dict:
    """Shadow-mode health summary for the KPI card; fail-soft so the KPI
    endpoint never breaks over validator data."""


    mode = str(os.getenv("THESIS_VALIDATOR_MODE", "shadow")).strip().lower() or "shadow"
    try:
        from market_db_modules.thesis_validator import summarize_thesis_validator_health

        health = summarize_thesis_validator_health()
    except Exception as exc:
        logger.warning("Failed to summarize thesis validator health: %s", exc)
        return {"available": False, "mode": mode}
    return {
        "available": health.get("position_day_count", 0) > 0,
        "mode": mode,
        **health,
    }


def total_unrealized_pl(positions: list) -> float | None:
    """The broker's unrealized P/L summed over open positions; None if any figure is missing."""
    values = [safe_float(position.get("unrealized_pl")) for position in positions]
    if any(value is None for value in values):
        return None
    return round(sum(values), 2)


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
    equity_curve = normalize_equity_curve(portfolio_history)
    daily_returns = compute_daily_returns(equity_curve)
    total_return_pct = compute_total_return_pct(equity_curve)
    sharpe_metrics = compute_sharpe_metrics(
        daily_returns,
        annual_risk_free_rate=DEFAULT_ANNUAL_RISK_FREE_RATE,
    )
    sortino_metrics = compute_sortino_metrics(
        daily_returns,
        annual_risk_free_rate=DEFAULT_ANNUAL_RISK_FREE_RATE,
    )
    trade_performance = compute_trade_metrics(fills)
    top_rankings = _build_top_rankings_payload()
    usage_summary = _build_usage_summary()
    market_regime = build_market_regime_payload()
    thesis_validator = _build_thesis_validator_kpi_payload()

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "account_equity": equity,
        "buying_power": buying_power,
        "day_pl": day_pl,
        "day_pl_pct": day_pl_pct,
        "unrealized_pl": total_unrealized_pl(normalized_positions),
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
        "performance_summary": {
            "total_return_pct": round(total_return_pct, 2) if total_return_pct is not None else None,
            "annualized_sharpe": round(sharpe_metrics["annualized_sharpe"], 4)
            if sharpe_metrics["annualized_sharpe"] is not None
            else None,
            "annualized_sortino": round(sortino_metrics["annualized_sortino"], 4)
            if sortino_metrics["annualized_sortino"] is not None
            else None,
            "profit_factor": trade_performance.get("profit_factor"),
            "average_trade_return_pct": trade_performance.get("average_trade_return_pct"),
        },
        "usage_summary": usage_summary,
        "bot_status": summarize_bot_status(read_bot_components()),
        "market_status": summarize_market_status(clock),
        "market_regime": market_regime,
        "top_rankings": top_rankings,
        "thesis_validator": thesis_validator,
    }
