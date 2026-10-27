from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.config import AlpacaSettings


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = ROOT_DIR / "web_dashboard" / "public" / "portfolio_history.json"


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
        with urlopen(request) as response:
            return json.load(response)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca request failed with status {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach Alpaca API: {exc.reason}") from exc


def write_output(data: dict, output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
