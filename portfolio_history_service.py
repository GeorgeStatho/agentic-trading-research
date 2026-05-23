from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
DEFAULT_OUTPUT_PATH = ROOT_DIR / "web_dashboard" / "public" / "portfolio_history.json"


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


def build_portfolio_history_url() -> str:
    base_url = (
        "https://paper-api.alpaca.markets"
        if env_flag("ALPACA_PAPER", True)
        else "https://api.alpaca.markets"
    )

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
    api_key = str(os.getenv("PUBLIC_KEY") or "").strip()
    api_secret = str(os.getenv("PRIVATE_KEY") or "").strip()

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
