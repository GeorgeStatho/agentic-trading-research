# Core Folder Guide

This folder holds the shared plumbing that the scraping pipelines reuse.

If `pipelines/` is where the orchestration lives, `core/` is where the basic reusable rules live.

## What This Folder Does

The code in this folder is responsible for:

- recency checks
- deterministic scoring helpers
- failed-URL recording
- scrape log configuration

## Files

### [CommonPipeline.py](CommonPipeline.py)

This is the main shared utility module for the news pipelines.

Important responsibilities:

- `is_recent_article(...)`
  - checks whether an article publish time is inside the allowed age window; `pipelines/_link_policy.py` applies it to URL and stored dates, `pipelines/_article_follow.py` to the extracted date. The window is the per-source `max_article_age_days`, defaulting to `MAX_ARTICLE_AGE_DAYS`

- `article_age_days(...)`
  - computes how old an article is in days

- `compute_article_scores(...)`
  - builds the deterministic score bundle stored with articles
  - uses recency, source reputation, directness, factuality, and confirmation helpers

- `build_source_url(...)`
  - fills source URL templates with an encoded query string

- `record_failed_url(...)`, `clear_failed_url(...)`
  - maintain the `failed_urls` rows; whether a failed URL is skipped is decided by `pipelines/_link_policy.py` (3 failures, or permanent)

Important constants:

- `MAX_ARTICLES_PER_SEARCH_PAGE = 10`
- `MAX_ARTICLE_AGE_DAYS = 14`

### [scrape_logging.py](scrape_logging.py)

This module creates the shared file logger used by the scraper.

Important behavior:

- creates one log file per session under `Data/logs/`
- exposes `get_scrape_logger(name)`
- exposes `get_scrapy_log_settings()`
- exposes `get_log_file_path()`

This is why the logs from source-page crawl, article-page crawl, and pipeline decisions all land in the same session log.

### [__init__.py](__init__.py)

Package marker for the folder.

## How The Folder Fits Into The System

Typical flow:

1. a pipeline crawls a source page
2. it gets back many discovered links
3. the pipeline keeps the relevant links
4. `judge_links(...)` in `pipelines/_link_policy.py` gives every link a fetch, reuse or skip verdict
5. the `fetch` articles are fetched
6. `compute_article_scores(...)` builds the evidence-related fields
7. failures are written through the failed-URL helpers

## Mental Model

`core/` is where the generic scraping rules live.

It does not decide which company or industry to scrape.

It does not do the HTML extraction itself.

It provides the shared rules that keep the pipelines consistent.
