from stringtoJson import generalWrite
from yfinance_client import LOGGER, stream_stock_prices
import json

from pathlib import Path
import sys

WRITER_DIR = Path(__file__).resolve().parents[2] / "WritingToDataBase"
if str(WRITER_DIR) not in sys.path:
    sys.path.append(str(WRITER_DIR))

DATA_DIR = Path(__file__).resolve().parents[3] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_helpers.market import add_company_price_snapshot, initialize_market_database

STOCKS_FILE = DATA_DIR / "stocks_data.json"

ETF_FILE=DATA_DIR/"sector_etf.json"

HELD_FILE=DATA_DIR/"held_data.json"


async def GettingStockPrice(stocks: list[str]):
    initialize_market_database()
    await stream_stock_prices(stocks, store_stock)


def store_stock(stockData: dict):
    stocks_data={}
    stock_id = stockData.get("id")
    if not stock_id:
        return

    stocks_data[stock_id] = stockData
    generalWrite(stockData, "id", STOCKS_FILE)
    snapshot_id = add_company_price_snapshot(str(stock_id), stockData)
    if snapshot_id is None:
        LOGGER.warning("Skipped DB stock snapshot for unknown company symbol %s", stock_id)
    print(json.dumps(stocks_data[stock_id], indent=2, sort_keys=True))
