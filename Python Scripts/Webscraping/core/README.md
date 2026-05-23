# Core Folder Guide

This folder holds the shared plumbing that the scraping pipelines reuse.

If `pipelines/` is where the orchestration lives, `core/` is where the basic reusable rules live.

## What This Folder Does

The code in this folder is responsible for:

- common article-link filtering rules
- duplicate detection against the news database
- recency checks
- deterministic scoring helpers
- failed-URL tracking
- scrape log configuration

## Files

### [CommonPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/CommonPipeline.py)

This is the main shared utility module for the news pipelines.

Important responsibilities:

- `filter_article_links(...)`
  - takes raw discovered links from a crawled page
  - keeps only links from allowed sources
  - enforces source-specific article URL patterns
  - skips previously failed URLs
  - can skip already-saved article URLs

- `is_recent_article(...)`
  - checks whether an article publish time is inside the allowed age window

- `article_age_days(...)`
  - computes how old an article is in days

- `compute_article_scores(...)`
  - builds the deterministic score bundle stored with articles
  - uses recency, source reputation, directness, factuality, and confirmation helpers

- `build_source_url(...)`
  - fills source URL templates with an encoded query string

- `fetch_existing_article_by_url(...)`
  - normalizes the URL
  - looks it up in `news_articles`
  - returns the existing stored article if present

- `record_failed_url(...)`, `clear_failed_url(...)`, `should_skip_failed_url(...)`
  - maintain the `failed_urls` tracking behavior

Important constants:

- `MAX_ARTICLES_PER_SEARCH_PAGE = 10`
- `MAX_ARTICLE_AGE_DAYS = 14`

### [scrape_logging.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/scrape_logging.py)

This module creates the shared file logger used by the scraper.

Important behavior:

- creates one log file per session under `Data/logs/`
- exposes `get_scrape_logger(name)`
- exposes `get_scrapy_log_settings()`
- exposes `get_log_file_path()`

This is why the logs from source-page crawl, article-page crawl, and pipeline decisions all land in the same session log.

### [__init__.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/__init__.py)

Package marker for the folder.

## How The Folder Fits Into The System

Typical flow:

1. a pipeline crawls a source page
2. it gets back many discovered links
3. `filter_article_links(...)` removes obviously bad ones
4. the pipeline checks the survivors for topic relevance
5. article pages are fetched
6. `compute_article_scores(...)` builds the evidence-related fields
7. failures are written through the failed-URL helpers

## Mental Model

`core/` is where the generic scraping rules live.

It does not decide which company or industry to scrape.

It does not do the HTML extraction itself.

It provides the shared rules that keep the pipelines consistent.
