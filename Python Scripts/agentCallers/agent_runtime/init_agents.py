from ollama import Client

researcher = Client("researcher")#picks what stocks should get a deep analaysis

questioner=Client("questioning")#asks deeper questions about the company, such as suppliers and what external factors may affect the stock

riskManager=Client("riskManager")#determiens the risk of the stock and blocks very risky decisions from continuing

marketScanner=Client("scanner")#From market data and overhead news, determiens what stocks may have the best potential for profit

newsAnalayst=Client("newsAnalyst")#summarizes news about a company or stock

executor=Client("executor")#determines whether to buy, sell or hold

stratgist=Client("strategist")#Determines from market sentiment and data what type of stragety should be for each set of stocks

manager=Client("manager") #Reviews logs and make sure consistent money is being generated. Determines from logs what agent is underperforming and adjusts values

urlBuilder=Client("url")#determines what url to visit for the resarcher to visit and research

worldNews=Client("worldNews")#periodically looks for breaking news, and feeds breaking news to researcher

