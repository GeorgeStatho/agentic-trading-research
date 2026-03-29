import asyncio
import json
import math
from pathlib import Path
import sys
from typing import Any

WRITER_DIR = Path(__file__).resolve().parents[2] / "WritingToDataBase"
if str(WRITER_DIR) not in sys.path:
    sys.path.append(str(WRITER_DIR))

DATA_DIR = Path(__file__).resolve().parents[3] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from market_db import add_company_price_snapshot, load_sector_tree
from stringtoJson import generalWrite
from yfinance_client import (
    LOGGER,
    fetch_company_fast_info,
    fetch_industry_snapshot,
    fetch_sector_snapshot,
    stream_stock_prices,
)

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
    await stream_stock_prices(stocks, store_stock)


def store_stock(stockData: dict):
    stock_id = stockData.get("id")
    if not stock_id:
        return

    stocks_data[stock_id] = stockData
    generalWrite(stockData, "id", STOCKS_FILE)
    snapshot_id = add_company_price_snapshot(str(stock_id), stockData)
    if snapshot_id is None:
        LOGGER.warning("Skipped DB stock snapshot for unknown company symbol %s", stock_id)
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
        info = fetch_company_fast_info(company)
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


def GetIndustryInfo(
    industry: str,
    *,
    include_company_details: bool = True,
    include_top_growth: bool = True,
    include_top_performing: bool = True,
    include_research_reports: bool = True,
) -> dict:
    snapshot = fetch_industry_snapshot(
        industry,
        include_top_growth=include_top_growth,
        include_top_performing=include_top_performing,
        include_research_reports=include_research_reports,
    )
    industry_info = snapshot["industry_info"]
    companies = _frame_to_records(snapshot["top_companies"])
    top_growth = _frame_to_records(snapshot["top_growth_companies"])
    top_performing = _frame_to_records(snapshot["top_performing_companies"])

    if include_company_details:
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
        "research_reports": snapshot["research_reports"],
        "companies": companies,
    }


def GetSectorInfo(
    sector: str,
    *,
    include_company_details: bool = True,
    include_top_growth: bool = True,
    include_top_performing: bool = True,
    include_research_reports: bool = True,
    include_sector_top_companies: bool = True,
) -> dict:
    snapshot = fetch_sector_snapshot(
        sector,
        include_research_reports=include_research_reports,
        include_sector_top_companies=include_sector_top_companies,
    )
    sector_info = snapshot["sector_info"]
    industries_df = snapshot["industries"]
    industries: dict[str, dict] = {}

    for industry_key, row in industries_df.iterrows():
        normalized_industry_key = str(industry_key)
        fallback_industry_name = normalized_industry_key.replace("-", " ").title()
        industry_record = {
            "name": _clean_value(row.get("name")) or fallback_industry_name,
            "symbol": _clean_value(row.get("symbol")),
            "market_weight": _clean_value(row.get("market weight")),
        }
        industry_record.update(
            GetIndustryInfo(
                normalized_industry_key,
                include_company_details=include_company_details,
                include_top_growth=include_top_growth,
                include_top_performing=include_top_performing,
                include_research_reports=include_research_reports,
            )
        )
        industry_record["name"] = industry_record.get("name") or fallback_industry_name
        industries[normalized_industry_key] = industry_record

    sector_name = _clean_value(getattr(sector_info, "name", None)) or sector.replace("-", " ").title()
    sector_payload = {
        sector: {
            "name": sector_name,
            "ticker_symbol": getattr(sector_info, "symbol", None),
            "top_companies": _frame_to_records(snapshot["top_companies"]),
            "research_reports": snapshot["research_reports"],
            "industries": industries,
        }
    }

    SECTORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECTORS_FILE.write_text(json.dumps(sector_payload, indent=2, sort_keys=True), encoding="utf-8")
    load_sector_tree(sector_payload)
    return sector_payload


def saveSectors(
    *,
    include_company_details: bool = True,
    include_top_growth: bool = True,
    include_top_performing: bool = True,
    include_research_reports: bool = True,
    include_sector_top_companies: bool = True,
) -> dict[str, dict]:
    all_sectors: dict[str, dict] = {}
    for sector in SECTORS:
        LOGGER.info("Starting sector save for %s", sector)
        all_sectors.update(
            GetSectorInfo(
                sector,
                include_company_details=include_company_details,
                include_top_growth=include_top_growth,
                include_top_performing=include_top_performing,
                include_research_reports=include_research_reports,
                include_sector_top_companies=include_sector_top_companies,
            )
        )
        LOGGER.info("Finished sector save for %s", sector)
    return all_sectors


if __name__ == "__main__":
    # asyncio.run(GettingStockPrice(["AAPL", "MSFT", "GOOG"]))
    print(
        json.dumps(
            saveSectors(
                include_company_details=False,
                include_top_growth=True,
                include_top_performing=True,
                include_research_reports=False,
                include_sector_top_companies=True,
            ),
            indent=2,
            sort_keys=True,
        )
    )
