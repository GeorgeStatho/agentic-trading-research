# Market Data Folder Guide

This folder contains the Yahoo market-data side of the project.

It sits next to the news scraper, but it solves a different problem.

## What This Folder Does

The code in this folder is responsible for:

- talking to `yfinance`
- throttling and retrying Yahoo requests
- loading sector, industry, and company data
- loading ranking data such as top growth and top performing companies
- streaming real-time price data
- storing price snapshots
- providing a small Yahoo news helper used by the company news pipeline

## Files

### [yfinance_client.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yfinance_client.py)

This is the request wrapper around `yfinance`.

Important responsibilities:

- creates a dedicated Yahoo request logger
- throttles calls through `YFinanceRequestHandler`
- retries failed calls with backoff
- exposes small focused fetch helpers

Important helpers:

- `fetch_company_fast_info(company)`
- `fetch_industry_snapshot(industry, ...)`
- `fetch_sector_snapshot(sector, ...)`
- `stream_stock_prices(stocks, on_message)`

This file is important because it centralizes the Yahoo request behavior instead of scattering raw `yfinance` calls everywhere.

### [yFinanceData.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yFinanceData.py)

This is the main sector/industry/company ingestion script.

Important responsibilities:

- calls the `yfinance_client` helpers
- cleans values such as `NaN`
- converts DataFrame-like results into record dictionaries
- enriches industry companies with fast company info
- builds sector payloads
- writes JSON snapshots to `Data/`
- writes sector and company data into the market database

Important entrypoints:

- `GetCompanyInfo(company)`
- `GetIndustryInfo(industry, ...)`
- `GetSectorInfo(sector, ...)`
- `saveSectors(...)`

### [yFinanceRealTime.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yFinanceRealTime.py)

This module handles streaming price updates.

Important behavior:

- initializes the market database
- subscribes to Yahoo websocket price updates
- writes snapshots to `stocks_data.json`
- stores price snapshots in the market database

This is the "live price stream" side of the Yahoo integration.

### [yFinanceNews.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yFinanceNews.py)

This is a very small Yahoo news helper.

Important behavior:

- asks `yfinance` for `Ticker.get_news(...)`
- reduces the raw Yahoo payload into `(title, url)` pairs
- keeps only URLs from sources your scraper already allows

This helper does not save articles itself.

It feeds extra article URLs into the company news pipeline.

### [urllist.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/urllist.py)

Auxiliary URL-list or constant holder used by the market-data side.

### [__init__.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/__init__.py)

Package marker for the folder.

## How The Folder Fits Into The System

There are really two subflows here.

### Market structure ingestion

1. request sector snapshot
2. enumerate industries inside the sector
3. request industry snapshot
4. optionally request per-company fast info
5. clean the payloads
6. write JSON snapshots
7. write normalized rows into the market database

### Real-time price streaming

1. open a Yahoo websocket
2. subscribe to symbols
3. receive messages
4. write snapshots to JSON and the database

### Yahoo news helper flow

1. request Yahoo news items for a ticker
2. extract titles and URLs
3. keep only supported source URLs
4. hand those article URLs to the company pipeline

## Mental Model

`market_data/` is not mainly about scraping HTML.

It is mainly about structured Yahoo payloads, normalization, and market database writes.
