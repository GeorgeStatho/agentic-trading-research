from __future__ import annotations


SEARCH_SOURCES = {
    "Motley Fool Quote": {
        "url": "https://www.fool.com/quote/{exchange}/{symbol}/",
        "type": "search",
        "company_specific": "fool_quote",
    },
    "CNBC Quote": {
        "url": "https://www.cnbc.com/quotes/{symbol}?qsearchterm={symbol}",
        "type": "search",
        "company_specific": "cnbc_quote",
    },
    "MarketWatch Search": {
        "url": "https://www.marketwatch.com/search?q={query}&ts=0&tab=All%20News",
        "type": "search",
    },
    "Barrons Search": {
        "url": "https://www.barrons.com/search?query={query}&quotequery={query}&search_keywords={query}&search_statement_type=typed",
        "type": "search",
    },
    "Morningstar Search": {
        "url": "https://www.morningstar.com/search?query={query}&page=1&format=Article",
        "type": "search",
    },
    "Business Insider News Search": {
        "url": "https://markets.businessinsider.com/news?q={query}",
        "type": "search",
    },
    "Investing Search": {
        "url": "https://www.investing.com/search/?q={query}",
        "type": "search",
    },
}

INDUSTRY_LISTING_SOURCES = {
    "Investing Markets": {
        "url": "https://www.investing.com/news/markets",
        "type": "listing",
    },
    "Morningstar Markets": {
        "url": "https://www.morningstar.com/markets",
        "type": "listing",
    },
}

COMPANY_NEWS_SOURCES = SEARCH_SOURCES
INDUSTRY_NEWS_SOURCES = {**INDUSTRY_LISTING_SOURCES, **SEARCH_SOURCES}
