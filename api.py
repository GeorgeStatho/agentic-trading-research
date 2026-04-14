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


def _alpaca_base_url() -> str:
    return (
        "https://paper-api.alpaca.markets"
        if env_flag("ALPACA_PAPER", True)
        else "https://api.alpaca.markets"
    )


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
