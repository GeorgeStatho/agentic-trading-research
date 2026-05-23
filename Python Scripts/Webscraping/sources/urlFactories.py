from __future__ import annotations


SEARCH_SOURCES = {
    "Demo Search": {
        "url": "https://example.com/demo/search?q={query}",
        "type": "search",
    },
    "Demo Company Profile": {
        "url": "https://example.com/demo/companies/{symbol}",
        "type": "search",
        "company_specific": "symbol_profile",
    },
}

INDUSTRY_LISTING_SOURCES = {
    "Demo Markets Listing": {
        "url": "https://example.com/demo/markets",
        "type": "listing",
    },
    "Demo Sector Listing": {
        "url": "https://example.org/demo/sectors",
        "type": "listing",
    },
}

COMPANY_NEWS_SOURCES = SEARCH_SOURCES
INDUSTRY_NEWS_SOURCES = {
    **INDUSTRY_LISTING_SOURCES,
    **SEARCH_SOURCES,
}
