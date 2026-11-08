from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.core.trading_config import AlpacaSettings


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = ROOT_DIR / "web_dashboard" / "public" / "portfolio_history.json"
TIMEOUT_SECONDS = 15
# Daily history barely moves within a minute; the health route and the KPIs both poll it.
HISTORY_TTL_SECONDS = 60
_HISTORY_CACHE: dict = {}


def build_portfolio_history_url() -> str:
    base_url = AlpacaSettings.from_env().trading_base_url
    query = urlencode(
        {
            "period": os.getenv("PORTFOLIO_HISTORY_PERIOD", "1M"),
            "timeframe": os.getenv("PORTFOLIO_HISTORY_TIMEFRAME", "1D"),
            "intraday_reporting": os.getenv("PORTFOLIO_HISTORY_INTRADAY_REPORTING", "market_hours"),
            "pnl_reset": os.getenv("PORTFOLIO_HISTORY_PNL_RESET", "per_day"),
        }
    )
    return f"{base_url}/v2/account/portfolio/history?{query}"


def fetch_portfolio_history() -> dict:
    cached = _HISTORY_CACHE.get("history")
    now = time.monotonic()
    if cached and now - cached[0] < HISTORY_TTL_SECONDS:
        return cached[1]
    alpaca = AlpacaSettings.from_env()
    api_key = alpaca.api_key
    api_secret = alpaca.api_secret

    if not api_key or not api_secret:
        raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in Stock-trading-experiment/.env")

    request = Request(
        build_portfolio_history_url(),
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca request failed with status {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach Alpaca API: {exc.reason}") from exc
    _HISTORY_CACHE["history"] = (now, payload)
    return payload


def write_output(data: dict, output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
