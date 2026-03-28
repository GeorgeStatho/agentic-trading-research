import asyncio
import json
import logging
import math
from pathlib import Path
import sys
import time
from typing import Any, cast

import yfinance as yf

WRITER_DIR = Path(__file__).resolve().parents[2] / "WritingToDataBase"
if str(WRITER_DIR) not in sys.path:
    sys.path.append(str(WRITER_DIR))

DATA_DIR = Path(__file__).resolve().parents[3] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from market_db import load_sector_tree
from stringtoJson import generalWrite

stocks_data: dict[str, dict] = {}
STOCKS_FILE = DATA_DIR / "stocks_data.json"
SECTORS_FILE = DATA_DIR / "sectors_companies.json"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("yfinance_data")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"yfinance_requests_{timestamp}.log"
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.info("Logging yfinance requests to %s", log_path)
    return logger


LOGGER = _build_logger()

SECTORS = [
    "basic-materials",
    "communication-services",
    "consumer-cyclical",
    "consumer-defensive",
    "energy",
    "financial-services",
    "healthcare",
    "industrials",
    "real-estate",
    "technology",
    "utilities",
]


class YFinanceRequestHandler:
    """Small throttled wrapper for yfinance calls.

    Yahoo will intermittently throttle or reject bursts of requests. This
    handler spaces requests out and retries a small number of times with
    backoff so one rate-limit response does not abort the whole sector load.
    """

    def __init__(self, min_interval_seconds: float = 0.35, max_retries: int = 3, base_backoff_seconds: float = 1.0):
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self._last_request_time = 0.0

    def _sleep_if_needed(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def run(self, func, *args, **kwargs):
        last_exc: Exception | None = None
        context = kwargs.pop("_context", getattr(func, "__name__", "yfinance_call"))
        for attempt in range(self.max_retries + 1):
            self._sleep_if_needed()
            try:
                LOGGER.info("yfinance request start: %s (attempt %s)", context, attempt + 1)
                result = func(*args, **kwargs)
                self._last_request_time = time.monotonic()
                LOGGER.info("yfinance request success: %s (attempt %s)", context, attempt + 1)
                return result
            except Exception as exc:
                last_exc = exc
                self._last_request_time = time.monotonic()
                LOGGER.warning(
                    "yfinance request failed: %s (attempt %s/%s): %s",
                    context,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
                if attempt >= self.max_retries:
                    raise

                normalized_error = str(exc).lower()
                backoff = self.base_backoff_seconds * (attempt + 1)
                if "too many requests" in normalized_error or "rate limit" in normalized_error:
                    backoff *= 2
                LOGGER.info("Sleeping %.2fs before retrying %s", backoff, context)
                time.sleep(backoff)

        if last_exc is not None:
            raise last_exc


REQUEST_HANDLER = YFinanceRequestHandler()


async def GettingStockPrice(stocks: list[str]):
    async with yf.AsyncWebSocket() as ws:
        await ws.subscribe(stocks)
        await ws.listen(store_stock)


def store_stock(stockData: dict):
    stock_id = stockData.get("id")
    if not stock_id:
        return

    stocks_data[stock_id] = stockData
    generalWrite(stockData, "id", STOCKS_FILE)
    print(json.dumps(stocks_data[stock_id], indent=2, sort_keys=True))


def _clean_value(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _safe_fast_info_get(info, key: str):
    try:
        return _clean_value(info.get(key))
    except Exception:
        return None


def _require_value(value: Any | None, context: str) -> Any:
    if value is None:
        raise ValueError(f"Missing yfinance value for {context}")
    return value


def _frame_to_records(frame) -> dict[str, dict]:
    if frame is None or getattr(frame, "empty", True):
        return {}

    records: dict[str, dict] = {}
    for row in frame.reset_index().to_dict(orient="records"):
        row = {key: _clean_value(value) for key, value in row.items()}
        symbol = row.get("symbol") or row.get("Symbol") or row.get("index")
        if symbol is None:
            continue
        normalized_symbol = str(symbol).strip().lstrip("$")
        if not normalized_symbol:
            continue
        row["symbol"] = normalized_symbol
        records[normalized_symbol] = row
    return records


def GetCompanyInfo(company: str) -> dict:
    try:
        company_info = cast(
            Any,
            _require_value(
                REQUEST_HANDLER.run(yf.Ticker, company, _context=f"Ticker({company})"),
                f"ticker:{company}",
            ),
        )
        info = cast(
            Any,
            _require_value(
                REQUEST_HANDLER.run(lambda: company_info.fast_info, _context=f"fast_info({company})"),
                f"fast_info:{company}",
            ),
        )
    except Exception as exc:
        LOGGER.warning("Company info fetch failed for %s: %s", company, exc)
        return {
            "symbol": company,
            "fetch_error": str(exc),
        }

    return {
        "symbol": company,
        "currency": _safe_fast_info_get(info, "currency"),
        "day_high": _safe_fast_info_get(info, "dayHigh"),
        "day_low": _safe_fast_info_get(info, "dayLow"),
        "exchange": _safe_fast_info_get(info, "exchange"),
        "last_price": _safe_fast_info_get(info, "lastPrice"),
        "market_cap": _safe_fast_info_get(info, "marketCap"),
        "open": _safe_fast_info_get(info, "open"),
        "previous_close": _safe_fast_info_get(info, "previousClose"),
        "quote_type": _safe_fast_info_get(info, "quoteType"),
        "regular_market_previous_close": _safe_fast_info_get(info, "regularMarketPreviousClose"),
        "ten_day_average_volume": _safe_fast_info_get(info, "tenDayAverageVolume"),
        "three_month_average_volume": _safe_fast_info_get(info, "threeMonthAverageVolume"),
        "timezone": _safe_fast_info_get(info, "timezone"),
        "year_high": _safe_fast_info_get(info, "yearHigh"),
        "year_low": _safe_fast_info_get(info, "yearLow"),
    }


def GetIndustryInfo(industry: str) -> dict:
    LOGGER.info("Loading industry payload for %s", industry)
    industry_info = cast(
        Any,
        _require_value(
            REQUEST_HANDLER.run(yf.Industry, industry, _context=f"Industry({industry})"),
            f"industry:{industry}",
        ),
    )
    companies = _frame_to_records(
        REQUEST_HANDLER.run(lambda: industry_info.top_companies, _context=f"industry.top_companies({industry})")
    )
    top_growth = _frame_to_records(
        REQUEST_HANDLER.run(lambda: industry_info.top_growth_companies, _context=f"industry.top_growth({industry})")
    )
    top_performing = _frame_to_records(
        REQUEST_HANDLER.run(
            lambda: industry_info.top_performing_companies,
            _context=f"industry.top_performing({industry})",
        )
    )

    for symbol, company_row in companies.items():
        company_details = GetCompanyInfo(symbol)
        company_row.update(company_details)

    industry_name = _clean_value(getattr(industry_info, "name", None)) or industry.replace("-", " ").title()

    return {
        "name": industry_name,
        "symbol": getattr(industry_info, "symbol", None),
        "sector_key": getattr(industry_info, "sector_key", None),
        "sector_name": getattr(industry_info, "sector_name", None),
        "top_companies": companies,
        "top_growth_companies": top_growth,
        "top_performing_companies": top_performing,
        "research_reports": REQUEST_HANDLER.run(
            lambda: industry_info.research_reports,
            _context=f"industry.research_reports({industry})",
        ),
        "companies": companies,
    }


def GetSectorInfo(sector: str) -> dict:
    LOGGER.info("Loading sector payload for %s", sector)
    sector_info = cast(
        Any,
        _require_value(
            REQUEST_HANDLER.run(yf.Sector, sector, _context=f"Sector({sector})"),
            f"sector:{sector}",
        ),
    )
    industries_df = cast(
        Any,
        _require_value(
            REQUEST_HANDLER.run(lambda: sector_info.industries, _context=f"sector.industries({sector})"),
            f"sector_industries:{sector}",
        ),
    )
    industries: dict[str, dict] = {}

    for industry_key, row in industries_df.iterrows():
        normalized_industry_key = str(industry_key)
        fallback_industry_name = normalized_industry_key.replace("-", " ").title()
        industry_record = {
            "name": _clean_value(row.get("name")) or fallback_industry_name,
            "symbol": _clean_value(row.get("symbol")),
            "market_weight": _clean_value(row.get("market weight")),
        }
        industry_record.update(GetIndustryInfo(normalized_industry_key))
        industry_record["name"] = industry_record.get("name") or fallback_industry_name
        industries[normalized_industry_key] = industry_record

    sector_name = _clean_value(getattr(sector_info, "name", None)) or sector.replace("-", " ").title()
    sector_payload = {
        sector: {
            "name": sector_name,
            "ticker_symbol": getattr(sector_info, "symbol", None),
            "top_companies": _frame_to_records(
                REQUEST_HANDLER.run(lambda: sector_info.top_companies, _context=f"sector.top_companies({sector})")
            ),
            "research_reports": REQUEST_HANDLER.run(
                lambda: sector_info.research_reports,
                _context=f"sector.research_reports({sector})",
            ),
            "industries": industries,
        }
    }

    SECTORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECTORS_FILE.write_text(json.dumps(sector_payload, indent=2, sort_keys=True), encoding="utf-8")
    load_sector_tree(sector_payload)
    return sector_payload

def saveSectors() -> dict[str, dict]:
    all_sectors: dict[str, dict] = {}
    for sector in SECTORS:
        LOGGER.info("Starting sector save for %s", sector)
        all_sectors.update(GetSectorInfo(sector))
        LOGGER.info("Finished sector save for %s", sector)
    return all_sectors

if __name__ == "__main__":
    # asyncio.run(GettingStockPrice(["AAPL", "MSFT", "GOOG"]))
    print(json.dumps(saveSectors(), indent=2, sort_keys=True))
