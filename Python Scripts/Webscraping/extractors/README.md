# Extractors Folder Guide

This folder contains source-specific HTML parsing logic.

If `engine/article_extraction.py` is the dispatcher, these modules are the source-specific parsers it dispatches to.

## What This Folder Does

The code in this folder is responsible for:

- recognizing source-specific URL shapes
- recognizing source-specific search/result pages
- extracting clean article links from those pages
- extracting article title, body, and publish time from site-specific HTML

## Common Pattern

Most extractor modules follow the same pattern:

- `is_<site>_url(...)`
- `is_<site>_article_url(...)`
- `response_looks_like_<site>_search(...)`
- `extract_<site>_search_links(response)`
- `extract_<site>_article(response)`

That consistency is intentional. It keeps the engine code simple.

## Files

### [marketwatch.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/extractors/marketwatch.py)

Handles MarketWatch.

Important behavior:

- extracts search results from MarketWatch search pages
- recognizes article URLs using MarketWatch path hints such as `/story/`
- can parse article data from `__NEXT_DATA__` JSON when available
- falls back to selector-based extraction when needed

This module is a good example of "prefer embedded structured data, then fall back to CSS selectors."

### [cnbc.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/extractors/cnbc.py)

Handles CNBC.

Important behavior:

- recognizes CNBC search pages
- extracts clean result-card links from the search UI
- uses a strict CNBC article URL regex
- can parse article content from embedded `window.__s_data`
- falls back to selector-based extraction if the state object is not available

### [barrons.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/extractors/barrons.py)

Handles Barrons.

Important behavior:

- extracts result links from Barrons search pages
- recognizes Barrons article URLs under `/articles/`
- detects anti-bot / JavaScript interstitial responses
- prefers JSON-LD `NewsArticle` data when available
- falls back to selectors and meta descriptions

This file is important because Barrons is one of the more fragile sources from an anti-bot perspective.

### [investing.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/extractors/investing.py)

Handles Investing.com.

Important behavior:

- recognizes Investing search pages
- extracts links from the `News` and `Analysis` result sections
- restricts article URLs to expected `news` and `analysis` paths
- extracts article content from article-specific containers and falls back to summary metadata

### [fool.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/extractors/fool.py)

Handles Motley Fool.

Important behavior:

- recognizes quote pages as source/discovery pages
- pulls related article links from JSON-LD `ItemList` data when possible
- falls back to article-card and generic anchor scanning
- extracts Fool article content from common body containers

This is the extractor that supports the company-specific Fool quote-page source strategy.

### [yahoo.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/extractors/yahoo.py)

Handles Yahoo Finance article pages.

Important behavior:

- recognizes Yahoo Finance article URL patterns
- tries JSON-LD `NewsArticle` data first
- falls back to Yahoo-specific content selectors and metadata summaries

Current note:

- the company pipeline no longer depends on scraping Yahoo-hosted pages for its normal Yahoo news path
- Yahoo is now mostly used as a discovery input for article URLs from supported external sources
- this extractor still exists as support code for direct Yahoo article responses when encountered

### [__init__.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/extractors/__init__.py)

Re-exports the extractor functions so the rest of the system can import them from one place.

## How The Folder Fits Into The System

Typical flow:

1. the crawler fetches a page
2. `engine/article_extraction.py` looks at the domain
3. if a site-specific extractor exists, it calls that extractor first
4. if that extractor succeeds, the normalized result is returned
5. if it fails, the engine falls back to the generic extraction path

For search pages:

1. the crawler fetches a source/search page
2. `engine/article_scraper.py` checks whether the page looks like a known search page
3. if yes, the matching `extract_<site>_search_links(...)` function is used
4. otherwise the engine falls back to generic anchor extraction

## Mental Model

`extractors/` is where the scraper learns site-specific HTML patterns.

When a source changes its page structure, this is usually the folder that needs to be adjusted.
