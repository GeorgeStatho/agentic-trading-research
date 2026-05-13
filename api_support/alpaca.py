from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from api_support.context import ALPACA_SETTINGS


def alpaca_base_url() -> str:
    return ALPACA_SETTINGS.trading_base_url


def alpaca_data_base_url() -> str:
    return ALPACA_SETTINGS.data_base_url


def alpaca_get_json(path: str, query: dict[str, str] | None = None):
    if not ALPACA_SETTINGS.api_key or not ALPACA_SETTINGS.api_secret:
        raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in Stock-trading-experiment/.env")

    url = f"{alpaca_base_url()}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"

    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": ALPACA_SETTINGS.api_key,
            "APCA-API-SECRET-KEY": ALPACA_SETTINGS.api_secret,
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


def alpaca_data_get_json(path: str, query: dict[str, str] | None = None):
    if not ALPACA_SETTINGS.api_key or not ALPACA_SETTINGS.api_secret:
        raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in Stock-trading-experiment/.env")

    url = f"{alpaca_data_base_url()}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"

    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": ALPACA_SETTINGS.api_key,
            "APCA-API-SECRET-KEY": ALPACA_SETTINGS.api_secret,
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
