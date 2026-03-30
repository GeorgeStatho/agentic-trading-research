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

### [urlFactories.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/sources/urlFactories.py)

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

### [source_config.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/sources/source_config.py)

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

### [__init__.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/sources/__init__.py)

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
