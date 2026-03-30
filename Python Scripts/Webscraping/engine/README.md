# Engine Folder Guide

This folder contains the crawler and extraction engine that the pipelines call.

If `pipelines/` decides what should happen, `engine/` is where the low-level page fetching and HTML-to-article normalization happen.

## What This Folder Does

The code in this folder is responsible for:

- running Scrapy crawls
- extracting links from source pages
- turning article HTML into normalized article results
- handling the article crawl subprocess path
- supporting listing-page link extraction

## Files

### [article_scraper.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_scraper.py)

This is the main crawl runner.

Important public helpers:

- `crawl_articles(urls)`
  - used for source-page crawling
  - runs the Scrapy process in-process
  - supports keyboard stop handling

- `crawl_article_pages(urls)`
  - used for article-page crawling
  - runs the crawl in a subprocess
  - returns `dict[str, ArticleExtractionResult]`
  - exists mainly to avoid Twisted reactor restart issues when doing follow-up article crawls

Important internal pieces:

- `ArticleSpider`
  - the Scrapy spider used for both source pages and article pages
  - requests each URL
  - calls `extract_from_response(...)`
  - also inventories links found on the page

- `extract_search_links(response)`
  - uses site-specific search extractors when available
  - falls back to generic anchor extraction otherwise

- `_KeyboardStopMonitor`
  - lets a user stop a long source-page crawl with `q`, `Esc`, or `Ctrl+C`

Important behavior to know:

- source-page crawl and article-page crawl use the same spider
- the difference is in how the pipeline interprets the results
- article-page crawling is isolated into a subprocess so it can be called safely after other crawling work

### [article_extraction.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_extraction.py)

This module converts a Scrapy `Response` into a normalized article result.

Main pieces:

- `ArticleExtractionResult`
  - dataclass with:
    - `url`
    - `title`
    - `text`
    - `published_at`
    - `success`
    - `error`

- `extract_from_response(response)`
  - the main dispatcher
  - tries site-specific extractors first
  - falls back to a generic paragraph-based extraction strategy

- `clean_text(...)`
  - normalizes whitespace and joins extracted text blocks

- `_normalize_datetime(...)`
  - normalizes many date/time formats into UTC ISO strings

- `_extract_published_at(response)`
  - checks common metadata tags and `time` elements

- `extract_article(url)`
  - a direct single-URL requests-based helper
  - useful outside the batch crawler path

Current site-specific extraction dispatch includes:

- MarketWatch
- Barrons
- CNBC
- Investing
- Fool
- Yahoo

### [listing_page_helper.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/listing_page_helper.py)

This file contains `extract_listing_article_links(...)`.

It is a smaller helper for sources that are listing pages rather than query-driven search pages.

It:

- checks source allowlist and `listing` support
- enforces article URL patterns
- matches topic text against link text or URL

### [Normalization.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/Normalization.py)

Compatibility shim for older imports that still expect the old module path.

### [__init__.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/__init__.py)

Package marker for the folder.

## How The Folder Fits Into The System

Typical flow:

1. a pipeline gives source URLs to `crawl_articles(...)`
2. `ArticleSpider` fetches those pages and emits page results with discovered links
3. the pipeline filters those links and decides which article URLs to follow
4. the article URLs are handed to `crawl_article_pages(...)`
5. the subprocess crawl returns normalized `ArticleExtractionResult` objects
6. the pipeline scores and saves those results

## Mental Model

`engine/` is the runtime machinery.

It is not where business rules for companies or industries live.

It is where "fetch this page" and "turn this HTML into a structured result" live.
