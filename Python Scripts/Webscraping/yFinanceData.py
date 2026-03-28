import asyncio
import json
import math
from pathlib import Path
import sys

import yfinance as yf

WRITER_DIR = Path(__file__).resolve().parents[1] / "WritingToDataBase"
if str(WRITER_DIR) not in sys.path:
    sys.path.append(str(WRITER_DIR))

DATA_DIR = Path(__file__).resolve().parents[2] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from market_db import load_sector_tree
from stringtoJson import generalWrite

stocks_data: dict[str, dict] = {}
STOCKS_FILE = DATA_DIR / "stocks_data.json"
SECTORS_FILE = DATA_DIR / "sectors_companies.json"

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
        company_info = yf.Ticker(company)
        info = company_info.fast_info
    except Exception as exc:
        return {
            "symbol": company,
            "fetch_error": str(exc),
        }

    return {
        "symbol": company,
        "currency": info.get("currency"),
        "day_high": info.get("dayHigh"),
        "day_low": info.get("dayLow"),
        "exchange": info.get("exchange"),
        "last_price": info.get("lastPrice"),
        "market_cap": info.get("marketCap"),
        "open": info.get("open"),
        "previous_close": info.get("previousClose"),
        "quote_type": info.get("quoteType"),
        "regular_market_previous_close": info.get("regularMarketPreviousClose"),
        "ten_day_average_volume": info.get("tenDayAverageVolume"),
        "three_month_average_volume": info.get("threeMonthAverageVolume"),
        "timezone": info.get("timezone"),
        "year_high": info.get("yearHigh"),
        "year_low": info.get("yearLow"),
    }


def GetIndustryInfo(industry: str) -> dict:
    industry_info = yf.Industry(industry)
    companies = _frame_to_records(industry_info.top_companies)
    top_growth = _frame_to_records(industry_info.top_growth_companies)
    top_performing = _frame_to_records(industry_info.top_performing_companies)

    for symbol, company_row in companies.items():
        company_details = GetCompanyInfo(symbol)
        company_row.update(company_details)

    return {
        "name": industry_info.name,
        "symbol": getattr(industry_info, "symbol", None),
        "sector_key": getattr(industry_info, "sector_key", None),
        "sector_name": getattr(industry_info, "sector_name", None),
        "top_companies": companies,
        "top_growth_companies": top_growth,
        "top_performing_companies": top_performing,
        "research_reports": industry_info.research_reports,
        "companies": companies,
    }


def GetSectorInfo(sector: str) -> dict:
    sector_info = yf.Sector(sector)
    industries_df = sector_info.industries
    industries: dict[str, dict] = {}

    for industry_key, row in industries_df.iterrows():
        industry_record = {
            "name": _clean_value(row.get("name")),
            "symbol": _clean_value(row.get("symbol")),
            "market_weight": _clean_value(row.get("market weight")),
        }
        industry_record.update(GetIndustryInfo(industry_key))
        industries[industry_key] = industry_record

    sector_payload = {
        sector: {
            "name": sector_info.name,
            "ticker_symbol": getattr(sector_info, "symbol", None),
            "top_companies": _frame_to_records(sector_info.top_companies),
            "research_reports": sector_info.research_reports,
            "industries": industries,
        }
    }

    SECTORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECTORS_FILE.write_text(json.dumps(sector_payload, indent=2, sort_keys=True), encoding="utf-8")
    load_sector_tree(sector_payload)
    return sector_payload


if __name__ == "__main__":
    # asyncio.run(GettingStockPrice(["AAPL", "MSFT", "GOOG"]))
    print(json.dumps(GetSectorInfo("basic-materials"), indent=2, sort_keys=True))
