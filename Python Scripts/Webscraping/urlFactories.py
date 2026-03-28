INDUSTRY_NEWS_SOURCES = {
    "investing_listing": {
        "type": "listing",
        "url": "https://www.investing.com/news/markets",
    },
    "morningstar_listing": {
        "type": "listing",
        "url": "https://www.morningstar.com/markets",
    },
    "fool_search": {
        "type": "search",
        "url": "https://www.fool.com/search/?q={query}",
    },
    "cnbc_finance_search": {
        "type": "search",
        "url": "https://www.cnbc.com/search/?query={query}",
    },
    "marketwatch_search": {
        "type": "search",
        "url": "https://www.marketwatch.com/search?q={query}&ts=0&tab=All%20News",
    },
    "barrons_search": {
        "type": "search",
        "url": "https://www.barrons.com/search?query={query}&quotequery={query}&search_keywords={query}&search_statement_type=typed",
    },
    "morningstar_search": {
        "type": "search",
        "url": "https://www.morningstar.com/search?query={query}&page=1&format=Article",
    },
    "markets_businessinsider_search": {
        "type": "search",
        "url": "https://markets.businessinsider.com/news?q={query}",
    },
    "investing_search": {
        "type": "search",
        "url": "https://www.investing.com/search/?q={query}",
    },
    "yahoo_finance_research": {
        "type": "search",
        "url": "https://finance.yahoo.com/research/",
    },
}

COMPANY_NEWS_SOURCES = {
    source_name: source_config
    for source_name, source_config in INDUSTRY_NEWS_SOURCES.items()
    if source_config["type"] == "search"
}
