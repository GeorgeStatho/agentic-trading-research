from alpaca.data import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
import json
from Keys import API_KEY,API_SECRECT_KEY

# keys required
stockHistoryClient = StockHistoricalDataClient(API_KEY, API_SECRECT_KEY)
stockRealTimeClient=StockDataStream(API_KEY,API_SECRECT_KEY)

#class that handles symbol and quote data
class StockData:
    
    def __init__(self,symbol:str):
        self.symbol=symbol
        self.latestPrice=0

    async def _LatestPriceHandler(self,data):
        self.latestPrice=data
        print(data)

    def SearchLatestPrice(self):
        stockRealTimeClient.subscribe_quotes(self._LatestPriceHandler(),self.symbol)
        stockRealTimeClient.run()

    def GetLatestPrice(self):
        return self.latestPrice

    def GetSymbol(self):
        return self.symbol

#gets symbols of companies from file, reads line by line
def getSymbols():
    symbols=[]
    with open('symbols.txt','r') as file:
        data=file.readline()
        while (data!=''):
            symbols.append(data)
            data=file.readline()
    file.close()
    return symbols

#Assembles StockData in an easy to use array
def PrepareAllStocksData():
    stockDatas=[]

    symbols=getSymbols()

    for i in symbols:
        stockData=StockData(i)
        stockDatas.append(stockData)
    
    return stockDatas

#incase I have to save Data to file
def WriteData(symbol:str,quote):
    with open("stockData.json","+r") as stockData:
        dic=json.load(stockData)
        dic[symbol]=quote
        stockData.seek(0)
        json.dump(dic,stockData,indent=4)
        stockData.truncate()
        print("Saved stockData of "+ symbol )
    
    


    

