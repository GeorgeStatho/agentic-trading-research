from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import DATA_DIR, ROOT_DIR, bootstrap_agent_callers, load_project_env


if __name__ == "__main__":
    load_project_env()

bootstrap_agent_callers(include_webscraping=True)

try:
    from alpaca.data import OptionHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import (
        OptionChainRequest,
        StockBarsRequest,
        StockLatestQuoteRequest,
        StockLatestTradeRequest,
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import ContractType
    from alpaca.trading.requests import GetOptionContractsRequest

    ALPACA_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - optional dependency
    OptionHistoricalDataClient = None
    OptionChainRequest = None
    StockBarsRequest = None
    StockHistoricalDataClient = None
    StockLatestQuoteRequest = None
    StockLatestTradeRequest = None
    TimeFrame = None
    TradingClient = None
    ContractType = None
    GetOptionContractsRequest = None
    ALPACA_IMPORT_ERROR = exc

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - optional dependency
    yf = None

try:
    from yfinance_client import REQUEST_HANDLER
except ImportError:  # pragma: no cover - optional dependency
    REQUEST_HANDLER = None

from agent_helpers.volatility import (
    compute_percentile_rank,
    compute_realized_volatility,
    summarize_option_iv,
)


DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE = max(1, int(os.getenv("MANAGER_OPTION_CHAIN_LIMIT_PER_TYPE", "6")))
DEFAULT_OPTION_CHAIN_FETCH_MULTIPLIER = max(2, int(os.getenv("MANAGER_OPTION_CHAIN_FETCH_MULTIPLIER", "6")))
DEFAULT_OPTION_FETCH_MIN = max(100, int(os.getenv("MANAGER_OPTION_FETCH_MIN", "500")))
PREFERRED_OTM_DISTANCE = 0.75
OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
MARKET_INDEX_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("sp500", "^GSPC", "S&P 500"),
    ("dow_jones_industrial_average", "^DJI", "Dow Jones Industrial Average"),
    ("vix", "^VIX", "CBOE Volatility Index"),
)
SECTOR_ETF_FILE = DATA_DIR / "sector_etfs.json"
CLOSEST_EXPIRATION_GTE = 1
FARTHEST_EXPIRATION_LTE = 8
UNDERLYING_HISTORY_LOOKBACK_CALENDAR_DAYS = max(
    90,
    int(os.getenv("MANAGER_UNDERLYING_HISTORY_LOOKBACK_DAYS", "120")),
)
UNDERLYING_HV_SHORT_WINDOW_DAYS = max(
    20,
    int(os.getenv("MANAGER_UNDERLYING_HV_SHORT_WINDOW_DAYS", "20")),
)
UNDERLYING_HV_LONG_WINDOW_DAYS = max(
    UNDERLYING_HV_SHORT_WINDOW_DAYS,
    int(os.getenv("MANAGER_UNDERLYING_HV_LONG_WINDOW_DAYS", "60")),
)
OPTION_IV_HISTORY_PATH = Path(
    os.getenv(
        "OPTION_IV_HISTORY_PATH",
        str(ROOT_DIR / "shared" / "option_iv_history.json"),
    )
)
OPTION_IV_HISTORY_MAX_ENTRIES = max(
    30,
    int(os.getenv("OPTION_IV_HISTORY_MAX_ENTRIES", "252")),
)
OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES = max(
    1,
    int(os.getenv("OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES", "20")),
)
IV_PERCENTILE_DTE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("1_3", 1, 3),
    ("4_7", 4, 7),
    ("8_14", 8, 14),
    ("15_30", 15, 30),
    ("31_45", 31, 45),
    ("46_60", 46, 60),
)

LOGGER = logging.getLogger("agent_helpers.market_context")
_ALPACA_CLIENTS: dict[str, Any] | None | bool = None


def _env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _serialize_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()

    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)) and enum_value is not None:
        return enum_value

    iso_value = getattr(value, "isoformat", None)
    if callable(iso_value):
        try:
            return iso_value()
        except TypeError:
            pass

    return str(value)


def _get_field(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _first_float(source: Any, *keys: str) -> float | None:
    for key in keys:
        value = _safe_float(_get_field(source, key))
        if value is not None:
            return value
    return None


def _normalize_identifier(value: Any) -> str:
    return str(value or "").strip().lower()


def _get_alpaca_clients() -> dict[str, Any] | None:
    global _ALPACA_CLIENTS
    if _ALPACA_CLIENTS is False:
        return None
    if isinstance(_ALPACA_CLIENTS, dict):
        return _ALPACA_CLIENTS

    if ALPACA_IMPORT_ERROR is not None:
        _ALPACA_CLIENTS = False
        return None

    api_key = str(os.getenv("PUBLIC_KEY") or "").strip()
    api_secret = str(os.getenv("PRIVATE_KEY") or "").strip()
    if not api_key or not api_secret:
        _ALPACA_CLIENTS = False
        return None

    os.environ.pop("APCA_OAUTH_TOKEN", None)
    os.environ.pop("ALPACA_OAUTH_TOKEN", None)

    paper = _env_flag("ALPACA_PAPER", True)
    _ALPACA_CLIENTS = {
        "stock": StockHistoricalDataClient(api_key, api_secret),
        "option": OptionHistoricalDataClient(api_key, api_secret),
        "trading": TradingClient(api_key=api_key, secret_key=api_secret, oauth_token=None, paper=paper),
    }
    return _ALPACA_CLIENTS
