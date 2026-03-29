import yfinance
from stringtoJson import generalWrite
from yfinance_client import (stream_stock_prices)
import json


async def GettingStockPrice(stocks: list[str]):
    await stream_stock_prices(stocks, store_stock)


def store_stock(stockData: dict):
    stocks_data={}
    stock_id = stockData.get("id")
    if not stock_id:
        return

    stocks_data[stock_id] = stockData
    generalWrite(stockData, "id", STOCKS_FILE)
    print(json.dumps(stocks_data[stock_id], indent=2, sort_keys=True))