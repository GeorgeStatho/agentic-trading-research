import asyncio
import json
from pathlib import Path

import yfinance as yf

stocks_data: dict[str, dict] = {}
DATA_FILE = Path(__file__).resolve().parents[2] / "Data" / "stocks_data.json"

SECTORS=["basic-materials",
         "communication-services",
         "consumer-cyclical",
         "consumer-defensive",
         "energy",
         "financial-services",
         "healthcare",
         "industrials",
         "real-estate",
         "technology",
         "utilities"]



async def GettingStockPrice(stocks: list[str]):
    async with yf.AsyncWebSocket() as ws:
        await ws.subscribe(stocks)
        await ws.listen(store_stock)


def store_stock(stockData: dict):
    stock_id = stockData.get("id")
    if not stock_id:
        return

    stocks_data[stock_id] = stockData
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(stocks_data, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(stocks_data[stock_id], indent=2, sort_keys=True))


def GetIndustryInfo(industry:str):
    industryInfo=yf.Industry(industry)
    print(industryInfo.name)
    print(industryInfo.top_companies)
    print(industryInfo.top_growth_companies)
    print(industryInfo.top_performing_companies)


def GetSectorInfo(sector:str):
    tech = yf.Sector(sector)
    #print(tech.name)
    #print(tech.industries)
    #print(tech.top_companies)
    print(tech.research_reports)
    industries_df = tech.industries

    #for row in industries_df.itertuples():
    #    industry_key = row.Index
    #    industry_name = row.name
    #    GetIndustryInfo(industry_key)

if __name__ == "__main__":
    #asyncio.run(GettingStockPrice(["AAPL", "MSFT", "GOOG"]))
    GetSectorInfo("basic-materials")
