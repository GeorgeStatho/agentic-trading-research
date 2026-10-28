# Engine Folder Guide

This folder contains the crawler and extraction engine that the pipelines call.

If `pipelines/` decides what should happen, `engine/` is where the low-level page fetching and HTML-to-article normalization happen.

## What This Folder Does

The code in this folder is responsible for:

- providing one fetch entry point for the pipelines (`page_fetch.py`)
- running Scrapy crawls
- extracting links from source pages
- turning article HTML into normalized article results
- handling the article crawl subprocess path

## Files

### [page_fetch.py](page_fetch.py)

This is the one entry point pipelines use to get pages from the network. Pipelines import these three functions and never call `crawl_articles` / `crawl_article_pages` or `urlopen` directly:

- `fetch_source_pages(urls)`
  - returns the list of page result dicts for search/listing pages
- `fetch_articles(urls)`
  - returns `dict[str, ArticleExtractionResult]`; the dict can carry extra keys, such as redirect final URLs
- `fetch_feed(url)`
  - returns the RSS/XML body as text, or `None`

Each call is forwarded to the current fetcher. By default that is `LiveFetcher`, which routes like this:

- `source_pages` -> `crawl_articles(urls)`
- `articles` -> the site API first, chosen by hostname (`fool.com`, `investing.com`, `morningstar.com`, `gurufocus.com`, via `ARTICLE_CONTENT_FETCHERS`), otherwise `crawl_article_pages(urls)`. A URL whose API call raises goes to the crawler instead. An API payload that is malformed or has an empty title/body is kept as a `success=False` result, not retried on the crawler.
- `feed` -> a plain `urlopen` GET (20 second timeout, utf-8 with replacement)

Whether Scrapy or Playwright does the crawling is decided inside `article_scraper.py`; `page_fetch` does not know about it.

Failure containment: `LiveFetcher` catches any `Exception` from a backend, logs a warning, and returns nothing for the affected URLs (`[]`, a dict without them, or `None`). `KeyboardInterrupt` still propagates. A crashed backend therefore never raises into a pipeline.

This leads to the "missing, not failed" rule:

- a URL that is absent from the result (the backend crashed or was unavailable) is only logged. The pipeline does not write it to `failed_urls`, so it is tried again on the next run.
- a page that was fetched but came back with `success=False` is a real failure. It is recorded in `failed_urls`, and `pipelines/_link_policy.py` gives it a `failed_url` skip verdict, on every path, once it has failed 3 times (or permanently, for HTTP 403 and 404).

The reason is that an infrastructure outage should not blacklist good URLs. The operational consequence is that a total outage shows up as "saved 0" plus warnings in the logs, not as an exception.

For tests, `use_fetcher(fetcher)` is a context manager that swaps the current fetcher (held in a `ContextVar`, so it does not carry into new threads). `FixtureFetcher(pages=..., articles=..., feeds=...)` answers from dicts, returns only the URLs it has fixtures for (so absent URLs behave like a crashed backend), never touches the network, and records every requested URL in `requested`:

```python
with use_fetcher(FixtureFetcher(feeds={feed_url: xml}, articles={url: result})):
    saved = get_us_news_from_rss()
```

### [article_scraper.py](article_scraper.py)

This is the main crawl runner. Pipelines reach it only through `page_fetch.py`.

Important public helpers:

- `crawl_articles(urls)`
  - used for source-page crawling
  - runs the Scrapy process in-process
  - supports keyboard stop handling
  - uses Playwright for the URLs that need it, and falls back to Scrapy if the Playwright backend raises any `Exception`

- `crawl_article_pages(urls)`
  - used for article-page crawling
  - runs the Scrapy crawl in a subprocess
  - returns `dict[str, ArticleExtractionResult]`
  - exists mainly to avoid Twisted reactor restart issues when doing follow-up article crawls
  - Playwright and Scrapy results are merged into one dict; a failing Playwright backend sends its URLs to Scrapy
  - a worker process that dies no longer leaves the call hanging
  - Playwright renders at most `WEBSCRAPING_PLAYWRIGHT_MAX_PAGES` (default 30) distinct URLs per call, 2 at a time, with images, media and fonts blocked; the rest are left out of the result, so they count as missing and are retried next run

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

### [article_extraction.py](article_extraction.py)

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

Current site-specific extraction dispatch includes:

- MarketWatch
- Barrons
- CNBC
- Investing
- Fool
- Yahoo

### [__init__.py](__init__.py)

Package marker for the folder.

## How The Folder Fits Into The System

Typical flow:

1. a pipeline gives source URLs to `fetch_source_pages(...)` in `page_fetch.py`, which calls `crawl_articles(...)`
2. `ArticleSpider` fetches those pages and emits page results with discovered links
3. the pipeline keeps the relevant links and `judge_links(...)` decides which article URLs to follow
4. the article URLs are handed to `fetch_articles(...)`, which tries the site APIs and sends the rest to `crawl_article_pages(...)`
5. the crawl returns normalized `ArticleExtractionResult` objects
6. the pipeline scores and saves those results

## Mental Model

`engine/` is the runtime machinery.

It is not where business rules for companies or industries live.

`page_fetch.py` is the seam between the two layers: pipelines ask it for pages, and tests swap it for a `FixtureFetcher`.

It is where "fetch this page" and "turn this HTML into a structured result" live.
