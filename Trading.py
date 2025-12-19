from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from Keys import API_KEY,API_SECRECT_KEY


def IntializeTradingClient(api_key:str,secret:str,paper:bool)->TradingClient:
    trading_client=TradingClient(api_key,secret,paper)
    return trading_client

trading_client=IntializeTradingClient(API_KEY,API_SECRECT_KEY,True)

#class to help keep track of trades
class StockTrades:
    def __init__(self,company:str,numOfStocks:float,timeInForce:str,limit:bool):
        self.company=company
        self.numOfStocks=0
        self.timeInForce=timeInForce
        limit=limit
    

    #########BUY###############

#Buy Stocks as soon as they are avaiable
#company must be a company symbol for 
#timeInForce take in 4 options, "Day","FOK","GTC","IOC"

    def ImmediateStockBuy(self,numOfStocks:int=0):
        if(self.timeInForce=="Day"):
            time_in_force=TimeInForce.DAY
        elif(self.timeInForce=="FOK"):
            time_in_force=TimeInForce.FOK
        elif(self.timeInForce=="GTC"):
            time_in_force=TimeInForce.GTC
        elif(self.timeInForce=="IOC"):
            time_in_force=TimeInForce.IOC

        market_order_data= MarketOrderRequest(symbol=self.company,
                                          qty=numOfStocks,
                                          side=OrderSide.BUY,
                                          time_in_force=time_in_force)

        market_order=trading_client.submit_order(order_data=market_order_data)
        self.numOfStocks=self.numOfStocks+numOfStocks


    #buy Stocks when at specific price
    #follows similar parameters to ImmediateStockBuy function,except
    #limit_price and notional are needed at the end of the function
    def StockAtPriceBuy(self,numOfStocks:int=0,limit_price:int=0,notional:int=0):
        if(self.timeInForce=="Day"):
            time_in_force=TimeInForce.DAY
        elif(self.timeInForce=="FOK"):
            time_in_force=TimeInForce.FOK
        elif(self.timeInForce=="GTC"):
            time_in_force=TimeInForce.GTC
        elif(self.timeInForce=="IOC"):
            time_in_force=TimeInForce.IOC
        
        limit_order_data=LimitOrderRequest(
                                        symbol=self.company,
                                        limit_price=limit_price,
                                        notional=notional,
                                        qty=numOfStocks,
                                        side=OrderSide.BUY,
                                        time_in_force=time_in_force)
        
        limit_order=trading_client.submit_order(order_data=limit_order_data)
        self.numOfStocks=self.numOfStocks+numOfStocks

##########BUY############

##########SELL###########

#Exact Same parameters as the Buy functions but it will sell instead

    def ImmediateStockSell(self,numOfStocks:int=0):
        if(self.timeInForce=="Day"):
            time_in_force=TimeInForce.DAY
        elif(self.timeInForce=="FOK"):
            time_in_force=TimeInForce.FOK
        elif(self.timeInForce=="GTC"):
            time_in_force=TimeInForce.GTC
        elif(self.timeInForce=="IOC"):
            time_in_force=TimeInForce.IOC

        market_order_data= MarketOrderRequest(symbol=self.company,
                                          qty=numOfStocks,
                                          side=OrderSide.SELL,
                                          time_in_force=time_in_force)

        market_order=trading_client.submit_order(order_data=market_order_data)
        self.numOfStocks=self.numOfStocks-numOfStocks


    def StockAtPriceSell(self,numOfStocks:int=0, limit_price:int=0,notional:int=0):
        if(self.timeInForce=="Day"):
            time_in_force=TimeInForce.DAY
        elif(self.timeInForce=="FOK"):
            time_in_force=TimeInForce.FOK
        elif(self.timeInForce=="GTC"):
            time_in_force=TimeInForce.GTC
        elif(self.timeInForce=="IOC"):
            time_in_force=TimeInForce.IOC
        
        limit_order_data=LimitOrderRequest(
                                        symbol=self.company,
                                        limit_price=limit_price,
                                        notional=notional,
                                        qty=numOfStocks,
                                        side=OrderSide.SELL,
                                        time_in_force=time_in_force)
        
        limit_order=trading_client.submit_order(order_data=limit_order_data)
        self.numOfStocks=self.numOfStocks-numOfStocks



##########SELL###########

        



