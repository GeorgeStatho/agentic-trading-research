from __future__ import annotations

import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = Path(__file__).resolve().parent
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from api_support.alpaca import alpaca_get_json
from portfolio_history_service import fetch_portfolio_history, load_env
from services.common import env_positive_int, safe_float


DEFAULT_ANNUAL_RISK_FREE_RATE = 0.045
DEFAULT_TRADING_DAYS_PER_YEAR = 252


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round_metric(value: float | None, digits: int = 4) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


def normalize_equity_curve(portfolio_history: dict[str, Any]) -> list[dict[str, Any]]:
    timestamps = portfolio_history.get("timestamp")
    equity_values = portfolio_history.get("equity")
    if not isinstance(timestamps, list) or not isinstance(equity_values, list):
        return []

    points: list[dict[str, Any]] = []
    for raw_timestamp, raw_equity in zip(timestamps, equity_values):
        equity = safe_float(raw_equity)
        if equity is None or equity <= 0:
            continue

        timestamp_seconds: int | None = None
        try:
            timestamp_seconds = int(raw_timestamp)
        except (TypeError, ValueError):
            timestamp_seconds = None

        iso_timestamp = ""
        if timestamp_seconds is not None:
            iso_timestamp = datetime.fromtimestamp(timestamp_seconds, tz=timezone.utc).isoformat()
        else:
            parsed = _parse_datetime(raw_timestamp)
            if parsed is not None:
                iso_timestamp = parsed.isoformat()

        points.append(
            {
                "timestamp": iso_timestamp,
                "equity": float(equity),
            }
        )

    return points


def compute_daily_returns(equity_curve: list[dict[str, Any]]) -> list[float]:
    returns: list[float] = []
    previous_equity: float | None = None
    for point in equity_curve:
        equity = safe_float(point.get("equity"))
        if equity is None or equity <= 0:
            continue
        if previous_equity not in (None, 0):
            returns.append((equity / previous_equity) - 1.0)
        previous_equity = equity
    return returns


def compute_total_return_pct(equity_curve: list[dict[str, Any]]) -> float | None:
    if len(equity_curve) < 2:
        return None
    starting_equity = safe_float(equity_curve[0].get("equity"))
    ending_equity = safe_float(equity_curve[-1].get("equity"))
    if starting_equity in (None, 0) or ending_equity is None:
        return None
    return ((ending_equity / starting_equity) - 1.0) * 100.0


def compute_max_drawdown_pct(equity_curve: list[dict[str, Any]]) -> float | None:
    if not equity_curve:
        return None

    peak_equity: float | None = None
    max_drawdown_ratio = 0.0
    for point in equity_curve:
        equity = safe_float(point.get("equity"))
        if equity is None:
            continue
        if peak_equity is None or equity > peak_equity:
            peak_equity = equity
            continue
        if peak_equity and peak_equity > 0:
            max_drawdown_ratio = max(max_drawdown_ratio, (peak_equity - equity) / peak_equity)

    return max_drawdown_ratio * 100.0


def compute_sharpe_metrics(
    daily_returns: list[float],
    *,
    annual_risk_free_rate: float = DEFAULT_ANNUAL_RISK_FREE_RATE,
    trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> dict[str, float | None]:
    if len(daily_returns) < 2:
        return {
            "daily_sharpe": None,
            "annualized_sharpe": None,
        }

    daily_risk_free_rate = annual_risk_free_rate / float(trading_days_per_year)
    excess_returns = [daily_return - daily_risk_free_rate for daily_return in daily_returns]
    daily_std_dev = statistics.stdev(excess_returns)
    if daily_std_dev == 0:
        return {
            "daily_sharpe": None,
            "annualized_sharpe": None,
        }

    daily_sharpe = statistics.mean(excess_returns) / daily_std_dev
    return {
        "daily_sharpe": daily_sharpe,
        "annualized_sharpe": daily_sharpe * math.sqrt(trading_days_per_year),
    }


def compute_sortino_metrics(
    daily_returns: list[float],
    *,
    annual_risk_free_rate: float = DEFAULT_ANNUAL_RISK_FREE_RATE,
    trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> dict[str, float | None]:
    if len(daily_returns) < 2:
        return {
            "daily_sortino": None,
            "annualized_sortino": None,
        }

    daily_risk_free_rate = annual_risk_free_rate / float(trading_days_per_year)
    excess_returns = [daily_return - daily_risk_free_rate for daily_return in daily_returns]
    downside_returns = [min(0.0, excess_return) for excess_return in excess_returns]
    downside_variance = sum(value * value for value in downside_returns) / len(downside_returns)
    downside_deviation = math.sqrt(downside_variance)
    if downside_deviation == 0:
        return {
            "daily_sortino": None,
            "annualized_sortino": None,
        }

    daily_sortino = statistics.mean(excess_returns) / downside_deviation
    return {
        "daily_sortino": daily_sortino,
        "annualized_sortino": daily_sortino * math.sqrt(trading_days_per_year),
    }


def fetch_recent_fills(*, page_size: int = 100) -> list[dict[str, Any]]:
    fills = alpaca_get_json(
        "/v2/account/activities/FILL",
        {"direction": "desc", "page_size": str(max(1, int(page_size)))},
    )
    return fills if isinstance(fills, list) else []


def compute_trade_metrics(fills: list[dict[str, Any]]) -> dict[str, float | int | None]:
    if not isinstance(fills, list):
        fills = []

    ordered_fills = sorted(
        fills,
        key=lambda fill: _parse_datetime(fill.get("transaction_time")) or datetime.min.replace(tzinfo=timezone.utc),
    )

    inventory: dict[str, dict[str, float]] = {}
    realized_returns_pct: list[float] = []
    winning_returns_pct: list[float] = []
    losing_returns_pct: list[float] = []
    closed_trades = 0
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0

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
        avg_cost = position["avg_cost"]
        realized_pl = (price - avg_cost) * closed_qty
        closed_trades += 1
        if avg_cost > 0:
            realized_return_pct = ((price / avg_cost) - 1.0) * 100.0
            realized_returns_pct.append(realized_return_pct)
            if realized_return_pct > 0:
                winning_returns_pct.append(realized_return_pct)
            elif realized_return_pct < 0:
                losing_returns_pct.append(realized_return_pct)

        if realized_pl > 0:
            wins += 1
            gross_profit += realized_pl
        elif realized_pl < 0:
            gross_loss += abs(realized_pl)

        position["qty"] = max(0.0, position["qty"] - qty)
        if position["qty"] == 0:
            position["avg_cost"] = 0.0

    win_rate_pct = ((wins / closed_trades) * 100.0) if closed_trades else None
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    average_trade_return_pct = statistics.mean(realized_returns_pct) if realized_returns_pct else None
    average_win_return_pct = statistics.mean(winning_returns_pct) if winning_returns_pct else None
    average_loss_return_pct = statistics.mean(losing_returns_pct) if losing_returns_pct else None

    return {
        "wins": wins,
        "closed_trades": closed_trades,
        "win_rate_pct": _round_metric(win_rate_pct, 2),
        "profit_factor": _round_metric(profit_factor, 4),
        "gross_profit": _round_metric(gross_profit, 2),
        "gross_loss": _round_metric(gross_loss, 2),
        "average_trade_return_pct": _round_metric(average_trade_return_pct, 2),
        "average_win_return_pct": _round_metric(average_win_return_pct, 2),
        "average_loss_return_pct": _round_metric(average_loss_return_pct, 2),
    }


def build_performance_metrics_payload(
    *,
    annual_risk_free_rate: float = DEFAULT_ANNUAL_RISK_FREE_RATE,
    trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
    fill_page_size: int = 100,
) -> dict[str, Any]:
    portfolio_history = fetch_portfolio_history()
    equity_curve = normalize_equity_curve(portfolio_history)
    daily_returns = compute_daily_returns(equity_curve)
    sharpe_metrics = compute_sharpe_metrics(
        daily_returns,
        annual_risk_free_rate=annual_risk_free_rate,
        trading_days_per_year=trading_days_per_year,
    )
    sortino_metrics = compute_sortino_metrics(
        daily_returns,
        annual_risk_free_rate=annual_risk_free_rate,
        trading_days_per_year=trading_days_per_year,
    )

    fills_error = ""
    fills: list[dict[str, Any]] = []
    try:
        fills = fetch_recent_fills(page_size=fill_page_size)
    except Exception as exc:
        fills_error = str(exc)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "portfolio_history_config": {
            "period": str(os.getenv("PORTFOLIO_HISTORY_PERIOD", "1M")).strip(),
            "timeframe": str(os.getenv("PORTFOLIO_HISTORY_TIMEFRAME", "1D")).strip(),
            "annual_risk_free_rate": annual_risk_free_rate,
            "trading_days_per_year": trading_days_per_year,
        },
        "equity_curve": {
            "sample_count": len(equity_curve),
            "starting_equity": _round_metric(safe_float(equity_curve[0].get("equity")) if equity_curve else None, 2),
            "ending_equity": _round_metric(safe_float(equity_curve[-1].get("equity")) if equity_curve else None, 2),
            "total_return_pct": _round_metric(compute_total_return_pct(equity_curve), 2),
            "max_drawdown_pct": _round_metric(compute_max_drawdown_pct(equity_curve), 2),
            "average_daily_return_pct": _round_metric(
                statistics.mean(daily_returns) * 100.0 if daily_returns else None,
                4,
            ),
        },
        "risk_metrics": {
            "daily_sharpe": _round_metric(sharpe_metrics["daily_sharpe"], 4),
            "annualized_sharpe": _round_metric(sharpe_metrics["annualized_sharpe"], 4),
            "daily_sortino": _round_metric(sortino_metrics["daily_sortino"], 4),
            "annualized_sortino": _round_metric(sortino_metrics["annualized_sortino"], 4),
        },
        "trade_metrics": {
            **compute_trade_metrics(fills),
            "fill_sample_count": len(fills),
            "fills_error": fills_error,
        },
    }


def main() -> int:
    load_env()
    annual_risk_free_rate = float(
        str(os.getenv("PERFORMANCE_METRICS_RISK_FREE_RATE", str(DEFAULT_ANNUAL_RISK_FREE_RATE))).strip()
    )
    fill_page_size = env_positive_int("PERFORMANCE_METRICS_FILL_PAGE_SIZE", 100)
    payload = build_performance_metrics_payload(
        annual_risk_free_rate=annual_risk_free_rate,
        fill_page_size=fill_page_size,
    )
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
