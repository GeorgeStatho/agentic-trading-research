# Sources Folder Guide

This folder defines what sources exist and how the rest of the scraper should think about them.

If the pipelines are asking "what should I scrape?", the code in this folder helps answer that question.

## What This Folder Does

The code in this folder is responsible for:

- defining the configured search and listing sources
- mapping URLs to source metadata
- deciding whether a source is allowed
- deciding whether a source supports `search` or `listing`
- providing article URL patterns and source-specific metadata

## Files

### [urlFactories.py](urlFactories.py)

This file defines the source templates used to build jobs.

Important structures:

- `SEARCH_SOURCES`
- `INDUSTRY_LISTING_SOURCES`
- `COMPANY_NEWS_SOURCES`
- `INDUSTRY_NEWS_SOURCES`

Examples of what lives here:

- CNBC search URL template
- MarketWatch search URL template
- Barrons search URL template
- Morningstar search URL template
- Business Insider news search template
- Investing search template
- Motley Fool quote-page template

This file does not perform crawling itself.

It defines where the pipelines should start.

### [source_config.py](source_config.py)

This file maps a URL to source metadata from `config/source.json`.

Important helpers:

- `is_allowed_source(url)`
- `supports_source_type(url, source_type)`
- `get_article_patterns(url)`
- `get_max_article_age_days(url, default_days)`
- `get_source_metadata(url)`

Important behavior:

- uses hostname matching against the source config
- supports both older and newer config schema shapes
- caches the loaded source config with `lru_cache`

This file is central to understanding why a link is allowed, rejected, or treated differently from another source.

### Which news domains are enabled, and why

`config/source.json` is the allowlist. As of 2026-10-28 the enabled news domains are:

| Domain | `provenance_source_type` | How articles arrive |
|---|---|---|
| `cnbc.com` | `original_reporting` | CNBC quote and search pages (rendered with Playwright) plus the U.S., world and sector RSS feeds |
| `fool.com` | `commentary` | the Fool quote page and the Fool API |
| `finance.yahoo.com` | `aggregator` | `yf.Search` news links; URL patterns `/news/` and `/articles/` (real Yahoo article URLs are mostly `/articles/...`) |
| `benzinga.com` | `aggregator` | Alpaca News API, with the body taken from the API response (see `APIs/alpacaNewsAPI.py`), never scraped |

`provenance_source_type` decides how `Data/news_db_modules/provenance.py::_origin` counts an article's root source:
- `aggregator` and `commentary` articles get the root `unverified`, so they never count as independent confirmation on their own;
- the exception is when the article text, or the Yahoo `provenance.publisher` stored in its `raw_json`, names a wire (Reuters, AP, PR Newswire, Business Wire, GlobeNewswire): the root is then that wire. Yahoo- and Benzinga-hosted press releases therefore still count once, as the original wire.

Kept off on purpose (re-check with `scripts/check_extractors.py`):
- `marketwatch.com`: its search page returns 401 to the scraper;
- `investing.com`: its search page returns 403;
- `morningstar.com`: about 40% of pages sit behind an AWS WAF challenge that only a JS browser passes. It may work on the server, where Chromium is installed;
- `barrons.com`: paywalled;
- `reuters.com`: blocks bots.

### [__init__.py](__init__.py)

Package marker and export surface for the folder.

## How The Folder Fits Into The System

Typical flow:

1. a pipeline chooses configured source definitions from `urlFactories.py`
2. those templates become source jobs
3. later, when links are discovered, `source_config.py` decides:
   - is this source allowed?
   - does it support the current source type?
   - does this URL look like an article URL for that source?
   - what reputation or max-age settings apply?

## Mental Model

`sources/` is the configuration and classification layer.

It does not fetch pages and it does not parse articles.

It tells the rest of the system what kinds of sources exist and how to treat them.
