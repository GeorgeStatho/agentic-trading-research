Huvle's Stock Trading Bot
This bot uses Alpaca's stock trading api to facilitate its trades and NewsMesh's article grabbing api to grab articles.
The bot feeds the stock price and news data from these two Api's into a ML algorithim to determine whether or not to make a trade

1. Setup
    1. Get your Api keys from alpaca and NewsMesh respectively, and put them into a file named "Keys.py" with two variables: API_KEY and API_SECRECT_KEY for alpaca, and NEWS_API_KEY for NewsMesh

    2. Set up a JSON Dictionary with the company's symbol(string) as the key, and the Company's name(string) as the value

2. Usage 
    A.
    1. Run MarketData.py in its own terminal to collect stock prices 
    2. Run main.py to facilitate trades and training of bot
    3. To switch from paper money to real money change Trading_client to intiliaze with False instead of true(located at top of Trading.py)

    B.(WIP)
    1. Run server.py in web_dashboard folder
    2. click button "Start MarketData.py"
    3. click button "Start main.py"

If any clarification is needed, or critcism, please feel free to ask or tell me! 