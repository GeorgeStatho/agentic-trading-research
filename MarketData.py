from alpaca.data import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
import json
from Keys import API_KEY,API_SECRECT_KEY
import os
import asyncio
# keys required
stockHistoryClient = StockHistoricalDataClient(API_KEY, API_SECRECT_KEY)
stockRealTimeClient = StockDataStream(API_KEY, API_SECRECT_KEY)

#class that handles symbol and quote data

def startUP():
    if not (os.path.isfile("stockData.json")):
        with open("stockData.json","w") as stock_file:
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
        print(data)

    def GetLatestPrice(self):
        return self.latestPrice

    def GetSymbol(self):
        return self.symbol

#gets symbols of companies from file, reads line by line
def getSymbols()->list[str]:
    symbols=[]
    with open('symbols.txt','r') as file:
        data=file.readline().strip("\n")
        print(data)
        while (data!=''):
            symbols.append(data)
            data=file.readline().strip("\n")
            print(data)
    file.close()
    return symbols

#incase I have to save Data to file
def WriteData(symbol:str,quote):
    with open("stockData.json","r+") as stockData:
        dic=json.load(stockData)
        dic[symbol]=quote
        stockData.seek(0)
        json.dump(dic,stockData,indent=4)
        stockData.truncate()
        print("Saved stockData of "+ symbol )
    
async def _wait_for_all(stocks: dict[str, StockData]):
    await asyncio.gather(*(sd.received.wait() for sd in stocks.values()))
    stockRealTimeClient.stop()


async def run_stream_once():
    startUP()
    stocks = {symbol: StockData(symbol) for symbol in getSymbols()}
    for symbol, sd in stocks.items():
        stockRealTimeClient.subscribe_quotes(sd._LatestPriceHandler, symbol)

    await asyncio.gather(
        stockRealTimeClient._run_forever(),
        _wait_for_all(stocks)

    )


if __name__ == "__main__":
    asyncio.run(run_stream_once())

