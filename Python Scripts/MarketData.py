from alpaca.data import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
import json
from Keys import API_KEY,API_SECRECT_KEY
import os
import asyncio
import logging

stockHistoryClient = StockHistoricalDataClient(API_KEY, API_SECRECT_KEY)
stockRealTimeClient = StockDataStream(API_KEY, API_SECRECT_KEY)


def startUP():
    if not os.path.isfile(os.path.join("..", "Data", "stockData.json")):
        with open(os.path.join("..", "Data", "stockData.json"), "w") as stock_file:
            dic={}
            json.dump(dic,stock_file)


class StockData:

    def __init__(self,symbol:str):
        self.symbol=symbol
        self.latestPrice=0
        self.received=asyncio.Event()

    async def _LatestPriceHandler(self,data):
        self.latestPrice=data.ask_price
        WriteData(self.symbol,self.latestPrice)
        self.received.set()
        logging.debug(f"Quote received {self.symbol}: {data}")

    def GetLatestPrice(self):
        return self.latestPrice

    def GetSymbol(self):
        return self.symbol


def getSymbols()->list[str]:
    symbols_path = os.path.join("..", "Data", "symbols.json")
    if not os.path.exists(symbols_path):
        return []
    with open(symbols_path, 'r') as file:
        try:
            data = json.load(file)
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [entry.get("symbol") for entry in data if isinstance(entry, dict) and "symbol" in entry]
    return []


def WriteData(symbol:str,quote):
    with open(os.path.join("..", "Data", "stockData.json"), "r+") as stockData:
        dic=json.load(stockData)
        dic[symbol]=quote
        stockData.seek(0)
        json.dump(dic,stockData,indent=4)
        stockData.truncate()


async def _wait_for_all(stocks: dict[str, StockData], cooldown_seconds: float = 2.0):
    await asyncio.gather(*(sd.received.wait() for sd in stocks.values()))
    await asyncio.sleep(cooldown_seconds)
    await stockRealTimeClient.stop_ws()


async def run_stream_once():
    startUP()
    stocks = {symbol: StockData(symbol) for symbol in getSymbols()}
    for symbol, sd in stocks.items():
        stockRealTimeClient.subscribe_quotes(sd._LatestPriceHandler, symbol)

    await asyncio.gather(
        stockRealTimeClient._run_forever(),
        _wait_for_all(stocks)
    )


async def run_stream_continuously():
    while True:
        try:
            await run_stream_once()
        except Exception as exc:
            logging.exception("Quote stream failed: %s", exc)
            await asyncio.sleep(2)
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("Starting continuous market data stream. Press Ctrl+C to stop.")
    try:
        asyncio.run(run_stream_continuously())
    except KeyboardInterrupt:
        logging.info("Market data stream stopped by user.")
