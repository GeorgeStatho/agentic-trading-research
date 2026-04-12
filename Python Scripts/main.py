from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest


PYTHON_SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
LOGS_DIR = DATA_DIR / "logs"
ENV_PATH = ROOT_DIR / ".env"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
STATUS_PATH = ROOT_DIR / "web_dashboard" / "public" / "script_status.json"

for path in (PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

load_dotenv(ENV_PATH)

from agentCallers.main import run_full_agent_stack


LOGGER = logging.getLogger("front_main")
DEFAULT_OPTION_ORDER_QTY = max(1, int(os.getenv("AGENT_OPTION_ORDER_QTY", "1")))
OPTION_CONTRACT_MULTIPLIER = 100
RUN_INTERVAL_SECONDS = 3 * 60 * 60
MARKET_RECHECK_SECONDS = 5 * 60


def _env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _build_log_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOGS_DIR / f"front_main_{timestamp}.log"


def _configure_logging(log_path: Path) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def write_status(state: str, message: str, **extra: Any) -> None:
    payload = {
        "state": state,
        "message": message,
        "pid": os.getpid(),
        "updated_at": datetime.now().isoformat(),
        **extra,
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATUS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)


def _get_trading_client() -> TradingClient:
    api_key = str(os.getenv("PUBLIC_KEY") or "").strip()
    api_secret = str(os.getenv("PRIVATE_KEY") or "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in .env")

    os.environ.pop("APCA_OAUTH_TOKEN", None)
    os.environ.pop("ALPACA_OAUTH_TOKEN", None)
    paper = _env_flag("ALPACA_PAPER", True)
    return TradingClient(api_key=api_key, secret_key=api_secret, oauth_token=None, paper=paper)


def market_is_open(trading_client: TradingClient) -> bool:
    try:
        clock = trading_client.get_clock()
        return bool(getattr(clock, "is_open", False))
    except Exception as exc:
        LOGGER.exception("Failed to check Alpaca market clock: %s", exc)
        return False


def _extract_selected_option_candidates(agent_result: dict[str, Any]) -> list[dict[str, Any]]:
    selected_options = agent_result.get("selected_options", {})
    companies = selected_options.get("companies", [])
    if isinstance(companies, list):
        return companies
    return []


def _build_order_candidates(agent_result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_option_symbols: set[str] = set()

    for company_result in _extract_selected_option_candidates(agent_result):
        decision = str(company_result.get("decision") or "").strip().lower()
        confidence = str(company_result.get("confidence") or "").strip().lower()
        selected_option = company_result.get("selected_option") or {}
        option_symbol = str(selected_option.get("symbol") or "").strip().upper()

        if decision not in {"call", "put"}:
            continue
        if confidence != "high":
            continue
        if not option_symbol or option_symbol in seen_option_symbols:
            continue

        seen_option_symbols.add(option_symbol)
        candidates.append(
            {
                "company_id": company_result.get("company_id"),
                "symbol": company_result.get("symbol"),
                "name": company_result.get("name"),
                "decision": decision,
                "confidence": confidence,
                "selected_option_id": company_result.get("selected_option_id"),
                "selected_expiration_date": company_result.get("selected_expiration_date"),
                "selected_strike_price": company_result.get("selected_strike_price"),
                "selected_option_source": company_result.get("selected_option_source"),
                "selected_option_symbol": option_symbol,
                "selected_option": selected_option,
                "reason": company_result.get("reason"),
            }
        )

    return candidates


def _submit_option_market_order(
    client: TradingClient,
    *,
    option_symbol: str,
    qty: int,
) -> dict[str, Any]:
    order_request = MarketOrderRequest(
        symbol=option_symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    order = client.submit_order(order_data=order_request)

    return {
        "id": str(getattr(order, "id", "")),
        "symbol": str(getattr(order, "symbol", option_symbol)),
        "asset_class": str(getattr(order, "asset_class", "")),
        "qty": str(getattr(order, "qty", qty)),
        "side": str(getattr(order, "side", "")),
        "type": str(getattr(order, "type", "")),
        "time_in_force": str(getattr(order, "time_in_force", "")),
        "status": str(getattr(order, "status", "")),
        "submitted_at": str(getattr(order, "submitted_at", "")),
    }


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_option_reference_price(selected_option: dict[str, Any]) -> float | None:
    latest_quote = selected_option.get("latest_quote", {})
    if isinstance(latest_quote, dict):
        for key in ("ask_price", "midpoint_price", "bid_price"):
            price = _safe_float(latest_quote.get(key))
            if price is not None and price > 0:
                return price

    for key in ("latest_trade_price", "close_price"):
        price = _safe_float(selected_option.get(key))
        if price is not None and price > 0:
            return price

    return None


def _get_available_buying_power(trading_client: TradingClient) -> float:
    account = trading_client.get_account()
    buying_power = _safe_float(getattr(account, "buying_power", None))
    if buying_power is None:
        raise RuntimeError("Unable to read buying_power from Alpaca account.")
    return buying_power


def execute_selected_option_trades(
    *,
    trading_client: TradingClient,
    order_candidates: list[dict[str, Any]],
    order_qty: int = DEFAULT_OPTION_ORDER_QTY,
) -> dict[str, Any]:
    executions: list[dict[str, Any]] = []
    available_buying_power = _get_available_buying_power(trading_client)
    max_deployable_buying_power = available_buying_power * 0.90
    remaining_deployable_buying_power = max_deployable_buying_power

    for candidate in order_candidates:
        option_symbol = str(candidate.get("selected_option_symbol") or "").strip().upper()
        if not option_symbol:
            continue

        selected_option = candidate.get("selected_option") or {}
        option_reference_price = _get_option_reference_price(selected_option)
        estimated_order_cost = (
            option_reference_price * OPTION_CONTRACT_MULTIPLIER * order_qty
            if option_reference_price is not None
            else None
        )

        if estimated_order_cost is None:
            LOGGER.info(
                "Skipping %s because no usable option price was available to estimate order cost.",
                option_symbol,
            )
            executions.append(
                {
                    **candidate,
                    "order_qty": order_qty,
                    "submitted": False,
                    "order": None,
                    "estimated_order_cost": None,
                    "available_buying_power": available_buying_power,
                    "max_deployable_buying_power": max_deployable_buying_power,
                    "remaining_deployable_buying_power": remaining_deployable_buying_power,
                    "error": "No usable option price was available to estimate order cost.",
                }
            )
            continue

        if estimated_order_cost > remaining_deployable_buying_power:
            LOGGER.info(
                "Skipping %s because estimated cost %.2f exceeds remaining deployable buying power %.2f (90%% of account buying power).",
                option_symbol,
                estimated_order_cost,
                remaining_deployable_buying_power,
            )
            executions.append(
                {
                    **candidate,
                    "order_qty": order_qty,
                    "submitted": False,
                    "order": None,
                    "estimated_order_cost": estimated_order_cost,
                    "available_buying_power": available_buying_power,
                    "max_deployable_buying_power": max_deployable_buying_power,
                    "remaining_deployable_buying_power": remaining_deployable_buying_power,
                    "error": "Estimated order cost exceeded 90% buying power allowance.",
                }
            )
            continue

        LOGGER.info(
            "Submitting BUY market order for %s via %s (company=%s, decision=%s, confidence=%s, estimated_cost=%.2f, remaining_buying_power_90=%.2f)",
            option_symbol,
            candidate.get("selected_option_source"),
            candidate.get("symbol"),
            candidate.get("decision"),
            candidate.get("confidence"),
            estimated_order_cost,
            remaining_deployable_buying_power,
        )

        try:
            order_summary = _submit_option_market_order(
                trading_client,
                option_symbol=option_symbol,
                qty=order_qty,
            )
            remaining_deployable_buying_power -= estimated_order_cost
            executions.append(
                {
                    **candidate,
                    "order_qty": order_qty,
                    "submitted": True,
                    "order": order_summary,
                    "estimated_order_cost": estimated_order_cost,
                    "available_buying_power": available_buying_power,
                    "max_deployable_buying_power": max_deployable_buying_power,
                    "remaining_deployable_buying_power": remaining_deployable_buying_power,
                    "error": "",
                }
            )
        except Exception as exc:
            LOGGER.exception("Failed to submit option order for %s: %s", option_symbol, exc)
            executions.append(
                {
                    **candidate,
                    "order_qty": order_qty,
                    "submitted": False,
                    "order": None,
                    "estimated_order_cost": estimated_order_cost,
                    "available_buying_power": available_buying_power,
                    "max_deployable_buying_power": max_deployable_buying_power,
                    "remaining_deployable_buying_power": remaining_deployable_buying_power,
                    "error": str(exc),
                }
            )

    return {
        "ran_at": datetime.now().isoformat(),
        "paper": _env_flag("ALPACA_PAPER", True),
        "order_qty": order_qty,
        "available_buying_power": available_buying_power,
        "max_deployable_buying_power": max_deployable_buying_power,
        "remaining_deployable_buying_power": remaining_deployable_buying_power,
        "submitted_count": sum(1 for execution in executions if execution.get("submitted")),
        "candidate_count": len(order_candidates),
        "executions": executions,
    }


def main(trading_client: TradingClient | None = None) -> dict[str, Any]:
    LOGGER.info("Starting full agent stack run")
    agent_result = run_full_agent_stack()
    LOGGER.info("Finished full agent stack run")

    order_candidates = _build_order_candidates(agent_result)
    LOGGER.info("Prepared %s selected option candidates for trading", len(order_candidates))

    trading_client = trading_client or _get_trading_client()
    trade_result = execute_selected_option_trades(
        trading_client=trading_client,
        order_candidates=order_candidates,
    )

    combined_result = {
        "ran_at": datetime.now().isoformat(),
        "agent_result": agent_result,
        "trade_result": trade_result,
    }

    agent_output_path = DATA_DIR / "agent_runner_output.json"
    with agent_output_path.open("w", encoding="utf-8") as handle:
        json.dump(agent_result, handle, ensure_ascii=True, indent=2)

    selected_options_output_path = DATA_DIR / "selected_options_output.json"
    with selected_options_output_path.open("w", encoding="utf-8") as handle:
        json.dump(agent_result.get("selected_options", {}), handle, ensure_ascii=True, indent=2)

    trade_output_path = DATA_DIR / "trade_execution_output.json"
    with trade_output_path.open("w", encoding="utf-8") as handle:
        json.dump(trade_result, handle, ensure_ascii=True, indent=2)

    combined_output_path = DATA_DIR / "front_main_output.json"
    with combined_output_path.open("w", encoding="utf-8") as handle:
        json.dump(combined_result, handle, ensure_ascii=True, indent=2)

    LOGGER.info("Saved agent output to %s", agent_output_path)
    LOGGER.info("Saved selected options output to %s", selected_options_output_path)
    LOGGER.info("Saved trade execution output to %s", trade_output_path)
    LOGGER.info("Saved combined front-main output to %s", combined_output_path)

    return combined_result


def main_loop() -> None:
    trading_client = _get_trading_client()
    LOGGER.info(
        "Starting front-facing main loop with interval=%s seconds and market recheck=%s seconds",
        RUN_INTERVAL_SECONDS,
        MARKET_RECHECK_SECONDS,
    )
    write_status(
        "starting",
        "Front-facing main loop started",
        run_interval_seconds=RUN_INTERVAL_SECONDS,
        market_recheck_seconds=MARKET_RECHECK_SECONDS,
    )

    while True:
        cycle_started_at = datetime.now()

        if not market_is_open(trading_client):
            LOGGER.info("Market is closed. Sleeping %s seconds before checking again.", MARKET_RECHECK_SECONDS)
            write_status(
                "paused",
                "Market is closed",
                sleep_seconds=MARKET_RECHECK_SECONDS,
                next_check_at=(datetime.now()).isoformat(),
            )
            time.sleep(MARKET_RECHECK_SECONDS)
            continue

        try:
            write_status("running", "Executing trading cycle")
            result = main(trading_client=trading_client)
            print(json.dumps(result, ensure_ascii=True, indent=2))
        except Exception as exc:
            write_status("error", f"Front-facing main cycle failed: {exc}")
            LOGGER.exception("Front-facing main cycle failed: %s", exc)

        elapsed_seconds = (datetime.now() - cycle_started_at).total_seconds()
        sleep_seconds = max(0, RUN_INTERVAL_SECONDS - elapsed_seconds)
        LOGGER.info("Cycle complete. Sleeping %.1f seconds until next run.", sleep_seconds)
        write_status(
            "paused",
            "Sleeping until next cycle",
            sleep_seconds=sleep_seconds,
            last_cycle_started_at=cycle_started_at.isoformat(),
        )
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    log_path = _build_log_path()
    _configure_logging(log_path)

    LOGGER.info("Front-facing main log started at %s", log_path)
    main_loop()
    print(f"Front-facing main log written to {log_path}")
