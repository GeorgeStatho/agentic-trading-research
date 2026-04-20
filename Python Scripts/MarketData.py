from __future__ import annotations

from pathlib import Path
import asyncio
import json
import logging
import os
import re
from typing import Any, Callable

from alpaca.data import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.live import OptionDataStream, StockDataStream
from alpaca.data.requests import OptionLatestQuoteRequest, StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import ContractType
from alpaca.trading.requests import GetOptionContractsRequest
from dotenv import load_dotenv


##########PATHS AND FILES##########

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = PROJECT_DIR / "Data"
ENV_PATH = PROJECT_DIR / ".env"

STOCK_DATA_PATH = DATA_DIR / "stockData.json"
OPTION_DATA_PATH = DATA_DIR / "optionData.json"
STOCK_SYMBOLS_PATH = DATA_DIR / "symbols.json"
OPTION_SYMBOLS_PATH = DATA_DIR / "optionSymbols.json"

OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{1,8}\d{6}[CP]\d{8}$")
DEFAULT_STREAM_COOLDOWN_SECONDS = 2.0
DEFAULT_RETRY_DELAY_SECONDS = 2.0
DEFAULT_LOOP_DELAY_SECONDS = 0.1
DEFAULT_STREAM_MODE = "option"

##########PATHS AND FILES##########


##########ENVIRONMENT AND CLIENTS##########

load_dotenv(ENV_PATH)

API_KEY = os.getenv("PUBLIC_KEY")
API_SECRET_KEY = os.getenv("PRIVATE_KEY")
ALPACA_PAPER = str(os.getenv("ALPACA_PAPER", "true")).strip().lower() not in {"0", "false", "no", "off", ""}


def _require_api_credentials() -> tuple[str, str]:
    if not API_KEY or not API_SECRET_KEY:
        raise RuntimeError(
            "Missing Alpaca API credentials. "
            f"Expected PUBLIC_KEY and PRIVATE_KEY in {ENV_PATH}."
        )
    return API_KEY, API_SECRET_KEY


_api_key, _api_secret_key = _require_api_credentials()

stockHistoryClient = StockHistoricalDataClient(_api_key, _api_secret_key)
stockRealTimeClient = StockDataStream(_api_key, _api_secret_key)

optionHistoryClient = OptionHistoricalDataClient(_api_key, _api_secret_key)
optionRealTimeClient = OptionDataStream(_api_key, _api_secret_key)
tradingClient = TradingClient(
    api_key=_api_key,
    secret_key=_api_secret_key,
    oauth_token=None,
    paper=ALPACA_PAPER,
)

##########ENVIRONMENT AND CLIENTS##########


##########DATA FILE HELPERS##########

def initialize_data_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_json_object_file(STOCK_DATA_PATH)
    _ensure_json_object_file(OPTION_DATA_PATH)


def startUP() -> None:
    initialize_data_files()


def _ensure_json_object_file(path: Path) -> None:
    if path.exists():
        return
    with path.open("w", encoding="utf-8") as handle:
        json.dump({}, handle)


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError:
            return default


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)


def _save_option_symbols(symbols: list[str]) -> None:
    existing_data = _load_json_file(OPTION_SYMBOLS_PATH, default={})
    if isinstance(existing_data, dict):
        saved_symbols = {str(key): existing_data[key] for key in existing_data.keys()}
    else:
        saved_symbols = {}

    for symbol in symbols:
        normalized_symbol = _validate_option_symbol(symbol)
        if normalized_symbol is None:
            continue
        saved_symbols[normalized_symbol] = saved_symbols.get(normalized_symbol, normalized_symbol)

    _write_json_file(OPTION_SYMBOLS_PATH, saved_symbols)

##########DATA FILE HELPERS##########


##########SYMBOL HELPERS##########

def _normalize_symbol_entries(raw_data: Any) -> list[str]:
    if isinstance(raw_data, dict):
        return [str(key) for key in raw_data.keys()]

    if isinstance(raw_data, list):
        normalized: list[str] = []
        for entry in raw_data:
            if isinstance(entry, dict) and "symbol" in entry:
                normalized.append(str(entry["symbol"]))
            elif isinstance(entry, str):
                normalized.append(entry)
        return normalized

    return []


def _load_symbol_list(symbols_path: Path) -> list[str]:
    raw_data = _load_json_file(symbols_path, default=[])
    return _normalize_symbol_entries(raw_data)


def _validate_option_symbol(symbol: str) -> str | None:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return None

    if OPTION_SYMBOL_PATTERN.match(normalized_symbol):
        return normalized_symbol

    logging.warning(
        "Skipping invalid option symbol '%s'. "
        "Alpaca option streams require full option contract symbols like AAPL260515C00200000.",
        symbol,
    )
    return None


def getSymbols() -> list[str]:
    return _load_symbol_list(STOCK_SYMBOLS_PATH)


def getOptionSymbols() -> list[str]:
    valid_symbols: list[str] = []
    for symbol in _load_symbol_list(OPTION_SYMBOLS_PATH):
        normalized_symbol = _validate_option_symbol(symbol)
        if normalized_symbol is not None:
            valid_symbols.append(normalized_symbol)
    return valid_symbols

##########SYMBOL HELPERS##########


##########WRITE QUOTES##########

def _write_price(data_path: Path, symbol: str, quote: float) -> None:
    saved_data = _load_json_file(data_path, default={})
    if not isinstance(saved_data, dict):
        saved_data = {}
    saved_data[symbol] = quote
    _write_json_file(data_path, saved_data)


def WriteData(symbol: str, quote: float) -> None:
    _write_price(STOCK_DATA_PATH, symbol, quote)


def WriteOptionData(symbol: str, quote: float) -> None:
    _write_price(OPTION_DATA_PATH, symbol, quote)

##########WRITE QUOTES##########


##########MARKET DATA CLASSES##########

class _QuoteDataBase:
    data_label = "quote"
    write_callback: Callable[[str, float], None]

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.latestPrice = 0.0
        self.received = asyncio.Event()

    async def _LatestPriceHandler(self, data) -> None:
        self.latestPrice = float(getattr(data, "ask_price", 0) or 0)
        self.write_callback(self.symbol, self.latestPrice)
        self.received.set()
        logging.debug("%s received %s: %s", self.data_label, self.symbol, data)

    def GetLatestPrice(self) -> float:
        return self.latestPrice

    def GetSymbol(self) -> str:
        return self.symbol


class StockData(_QuoteDataBase):
    data_label = "Stock quote"
    write_callback = staticmethod(WriteData)

    def LoadLatestPrice(self) -> float:
        request = StockLatestQuoteRequest(symbol_or_symbols=self.symbol)
        quotes = stockHistoryClient.get_stock_latest_quote(request)
        quote = quotes.get(self.symbol)
        if quote is None:
            return self.latestPrice

        self.latestPrice = float(getattr(quote, "ask_price", 0) or 0)
        WriteData(self.symbol, self.latestPrice)
        return self.latestPrice


class OptionsData(_QuoteDataBase):
    data_label = "Option quote"
    write_callback = staticmethod(WriteOptionData)

    def LoadLatestPrice(self) -> float:
        request = OptionLatestQuoteRequest(symbol_or_symbols=self.symbol)
        quotes = optionHistoryClient.get_option_latest_quote(request)
        quote = quotes.get(self.symbol)
        if quote is None:
            return self.latestPrice

        self.latestPrice = float(getattr(quote, "ask_price", 0) or 0)
        WriteOptionData(self.symbol, self.latestPrice)
        return self.latestPrice

    @staticmethod
    def _extract_contract_symbols(contracts_response: Any) -> list[str]:
        if isinstance(contracts_response, dict):
            contract_items = contracts_response.get("option_contracts", [])
        else:
            contract_items = getattr(contracts_response, "option_contracts", contracts_response)

        if not isinstance(contract_items, list):
            try:
                contract_items = list(contract_items)
            except TypeError:
                contract_items = []

        symbols: list[str] = []
        for contract in contract_items:
            if isinstance(contract, dict):
                raw_symbol = contract.get("symbol")
            else:
                raw_symbol = getattr(contract, "symbol", None)

            normalized_symbol = _validate_option_symbol(str(raw_symbol or ""))
            if normalized_symbol is not None:
                symbols.append(normalized_symbol)

        return symbols

    @classmethod
    def GetOptionsForCompany(
        cls,
        company: str,
        *,
        contract_type: str,
        expiration_date: str,
        limit: int = 10,
    ) -> list["OptionsData"]:
        normalized_contract_type = str(contract_type or "").strip().lower()
        contract_type_map = {
            "call": ContractType.CALL,
            "put": ContractType.PUT,
        }
        if normalized_contract_type not in contract_type_map:
            raise ValueError("contract_type must be either 'call' or 'put'.")

        contracts_response = tradingClient.get_option_contracts(
            GetOptionContractsRequest(
                underlying_symbols=[company],
                type=contract_type_map[normalized_contract_type],
                expiration_date=expiration_date,
                limit=limit,
            )
        )
        contract_symbols = cls._extract_contract_symbols(contracts_response)
        _save_option_symbols(contract_symbols)
        return [cls(symbol) for symbol in contract_symbols]

    @classmethod
    def GetCallOptionsForCompany(
        cls,
        company: str,
        *,
        expiration_date: str,
        limit: int = 10,
    ) -> list["OptionsData"]:
        return cls.GetOptionsForCompany(
            company,
            contract_type="call",
            expiration_date=expiration_date,
            limit=limit,
        )

    @classmethod
    def GetPutOptionsForCompany(
        cls,
        company: str,
        *,
        expiration_date: str,
        limit: int = 10,
    ) -> list["OptionsData"]:
        return cls.GetOptionsForCompany(
            company,
            contract_type="put",
            expiration_date=expiration_date,
            limit=limit,
        )
##########MARKET DATA CLASSES##########


##########STREAM HELPERS##########

async def _wait_for_all(
    receivers: dict[str, _QuoteDataBase],
    stream: Any,
    cooldown_seconds: float = DEFAULT_STREAM_COOLDOWN_SECONDS,
) -> None:
    await asyncio.gather(*(receiver.received.wait() for receiver in receivers.values()))
    await asyncio.sleep(cooldown_seconds)
    await stream.stop_ws()


async def _run_stream_once(
    *,
    symbols: list[str],
    stream: Any,
    data_factory: Callable[[str], _QuoteDataBase],
    symbol_kind: str,
) -> None:
    initialize_data_files()

    receivers = {symbol: data_factory(symbol) for symbol in symbols}
    if not receivers:
        logging.warning("No valid %s symbols were found to stream.", symbol_kind)
        return

    for symbol, receiver in receivers.items():
        stream.subscribe_quotes(receiver._LatestPriceHandler, symbol)

    await asyncio.gather(
        stream._run_forever(),
        _wait_for_all(receivers, stream),
    )

##########STREAM HELPERS##########


##########RUN STREAM ONCE##########

async def run_stock_stream_once() -> None:
    await _run_stream_once(
        symbols=getSymbols(),
        stream=stockRealTimeClient,
        data_factory=StockData,
        symbol_kind="stock",
    )


async def run_option_stream_once() -> None:
    option_symbols = getOptionSymbols()
    if not option_symbols:
        logging.warning(
            "No valid option symbols were found in %s. "
            "Use full contract symbols, not underlying tickers.",
            OPTION_SYMBOLS_PATH,
        )
        return

    await _run_stream_once(
        symbols=option_symbols,
        stream=optionRealTimeClient,
        data_factory=OptionsData,
        symbol_kind="option",
    )

##########RUN STREAM ONCE##########


##########RUN STREAM CONTINUOUSLY##########

async def _run_stream_continuously(
    run_once: Callable[[], Any],
    *,
    stream_label: str,
) -> None:
    while True:
        try:
            await run_once()
        except Exception as exc:
            logging.exception("%s quote stream failed: %s", stream_label, exc)
            await asyncio.sleep(DEFAULT_RETRY_DELAY_SECONDS)
        await asyncio.sleep(DEFAULT_LOOP_DELAY_SECONDS)


async def run_stock_stream_continuously() -> None:
    await _run_stream_continuously(run_stock_stream_once, stream_label="Stock")


async def run_option_stream_continuously() -> None:
    await _run_stream_continuously(run_option_stream_once, stream_label="Option")

##########RUN STREAM CONTINUOUSLY##########


##########SCRIPT ENTRY##########

def _get_default_stream_runner() -> tuple[str, Callable[[], Any]]:
    if DEFAULT_STREAM_MODE == "stock":
        return "stock", run_stock_stream_continuously
    return "option", run_option_stream_continuously


if __name__ == "__main__":

    calls = OptionsData.GetCallOptionsForCompany(
    "AAPL",
    expiration_date="2026-05-15",
    limit=10,
    )

    puts = OptionsData.GetPutOptionsForCompany(
        "AAPL",
        expiration_date="2026-05-15",
        limit=10,
    )

    contracts = OptionsData.GetOptionsForCompany(
        "AAPL",
        contract_type="call",
        expiration_date="2026-05-15",
        limit=10,
    )


    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    stream_mode, stream_runner = _get_default_stream_runner()
    logging.info(
        "Starting continuous %s market data stream. Press Ctrl+C to stop.",
        stream_mode,
    )
    try:
        asyncio.run(stream_runner())
    except KeyboardInterrupt:
        logging.info("Market data stream stopped by user.")

##########SCRIPT ENTRY##########
