import asyncio
import yfinance as yf

async def GettingStockPrice(stocks:str[]):
    async with yf.AsyncWebSocket() as ws:
        await ws.subscribe(stocks)
        await ws.listen()

def GetSectorInfo(sector:str):
    
