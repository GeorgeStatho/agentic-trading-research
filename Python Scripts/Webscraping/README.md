# Webscraping Pipeline Guide

This README is meant to answer one question clearly:

"When I run one of these pipelines, what actually happens?"

This folder contains two related systems:

1. the news/article scraping pipelines
2. the Yahoo market-data ingestion code

They live in the same area of the repo, but they are not the same workflow.

## Big Picture

The news side is responsible for:

- choosing which source URLs to crawl
- crawling source pages such as search or listing pages
- extracting candidate article links from those pages
- filtering those links aggressively
- crawling the article pages that survive filtering
- extracting title, body, and publish time
- scoring and saving the result into the news database

The Yahoo market-data side is responsible for:

- fetching sector, industry, company, ranking, and price data from Yahoo
- cleaning and normalizing that data
- storing it in the market database

There is also a Yahoo news helper now, but it is only used as an extra article discovery input for the company pipeline. It does not drive the full pipeline by itself.

## Folder Layout

The most important current files are:

- [pipelines/companyNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/companyNewsPipeline.py)
- [pipelines/industryNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/industryNewsPipeline.py)
- [pipelines/macroNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/macroNewsPipeline.py)
- [pipelines/job_builder.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/job_builder.py)
- [pipelines/_shared.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_shared.py)
- [pipelines/_internal.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_internal.py)
- [pipelines/_constants.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_constants.py)
- [engine/article_scraper.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_scraper.py)
- [engine/article_extraction.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_extraction.py)
- [market_data/yFinanceNews.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yFinanceNews.py)

There are also top-level compatibility wrappers that keep older imports working:

- [companyNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/companyNewsPipeline.py)
- [industryNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/industryNewsPipeline.py)
- [macroNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/macroNewsPipeline.py)
- [article_scraper.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/article_scraper.py)
- [Normalization.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/Normalization.py)

Those wrappers matter because some older modules still import the old names.

## Core Mental Model

The easiest way to understand the news pipelines is to separate them into three layers:

1. planning
2. crawling
3. extraction and persistence

Planning decides what URLs should be visited.

Crawling fetches those URLs and returns structured page results.

Extraction and persistence decide which results are worth keeping, normalize the article content, score it, and write it to the database.

## Job Shape

The planning layer now builds explicit job dictionaries instead of having each pipeline assemble URLs ad hoc.

Job building lives in [pipelines/job_builder.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/job_builder.py).

Important helpers:

- `build_company_source_jobs(...)`
- `build_industry_source_jobs(...)`
- `build_company_source_job(...)`
- `build_yahoo_news_jobs(...)`
- `group_jobs_by_url(...)`
- `unique_job_urls(...)`

For companies, the job shape is:

```python
{
    "url": "...",
    "company": {...},
    "source_name": "...",
    "source_type": "search" or "article",
    "search_term": "...",
}
```

The most important field is `source_type`.

- `search` means "crawl this source page first, then look for article links on it"
- `article` means "this URL is already an article URL, so it can go straight into the article crawl batch"

## Shared Helpers

[pipelines/_shared.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_shared.py) is mostly a stable import surface. It re-exports the helpers the pipelines rely on, including:

- `crawl_articles(...)`
- `crawl_article_pages(...)`
- `filter_article_links(...)`
- `fetch_existing_article_by_url(...)`
- `compute_article_scores(...)`
- `is_recent_article(...)`
- `is_allowed_source(...)`
- `supports_source_type(...)`
- `get_source_metadata(...)`

[pipelines/_internal.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_internal.py) contains small pipeline-only helpers such as:

- company and industry match normalization
- CNBC blacklist checks
- "does this link text or URL actually mention the target thing?" logic

[pipelines/_constants.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_constants.py) holds shared constants such as company suffixes and industry stopwords.

## Company Pipeline Flow

The main entrypoints are:

- `get_company_news(company_identifier)`
- `get_all_company_news()`

Both live in [pipelines/companyNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/companyNewsPipeline.py).

### Step 1: Load company records

The pipeline pulls company records from the database with `get_all_companies()` or resolves one company with `_find_company(...)`.

Each company record carries the metadata needed for matching and persistence:

- `id`
- `name`
- `symbol`
- `industry_key`
- `industry_name`
- `sector_key`
- `sector_name`

### Step 2: Build jobs

`_build_company_jobs(company)` combines two sources of jobs:

1. normal configured company source jobs from `build_company_source_jobs([company])`
2. Yahoo-discovered article jobs from `yFinanceNews.py`

The Yahoo portion works like this:

1. `get_company_news_items(symbol)` calls `yfinance` and gets raw Yahoo news items
2. `extract_title_and_url(...)` reduces those items down to `(title, url)` pairs
3. only URLs from already-allowed scraper sources survive that filter
4. `build_yahoo_news_jobs(...)` turns those pairs into normal `article` jobs

That means Yahoo is now acting as an extra article discovery source, not as a special storage path.

### Step 3: Split jobs by type

`_process_company_jobs(...)` splits the full job list into:

- `search_jobs`
- `direct_article_jobs`

This is one of the most important design ideas in the current company pipeline.

Some inputs are discovery pages.

Some inputs are already article URLs.

The pipeline keeps those separate until it is time to batch the article crawl.

### Step 4: Crawl all search pages together

If there are search jobs, the pipeline groups them by URL and crawls all source pages in one call:

- `crawl_articles(urls)`

This happens in [engine/article_scraper.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_scraper.py).

This source-page crawl returns one structured result per crawled page, including:

- `request_url`
- `url`
- `title`
- `text`
- `published_at`
- `links`
- `success`
- `error`

For search pages, the key field is `links`.

### Step 5: Filter candidate links

The company pipeline does not follow every discovered link.

It first runs `_filter_company_candidate_links(...)`, which applies:

1. the shared `filter_article_links(...)` rules
2. company-specific match rules
3. CNBC blacklist rules

The company-specific match rules are built from `_build_company_match_variants(...)`.

Those variants include combinations of:

- company name
- company name with suffixes trimmed
- company ticker symbol

The goal is to avoid following generic market pages that do not really mention the target company.

### Step 6: Build one shared article crawl batch

This is the current important behavior in the company pipeline.

After the search pages are crawled, the pipeline builds a single combined list of article-save requests in `_build_article_save_requests(...)`.

That combined list includes:

- filtered article links discovered from the search pages
- Yahoo article jobs that were already direct article URLs

Then `_collect_article_urls_to_fetch(...)` extracts the article URLs that still need network fetches and skips:

- disallowed sources
- already-saved articles
- duplicates

Then the pipeline runs one shared article crawl:

- `crawl_article_pages(article_urls)`

This keeps Yahoo-discovered article URLs in the same article crawl batch as the URLs discovered from normal search pages.

### Step 7: Save followed articles

Each article-save request is processed by `_save_followed_article_links(...)`.

That function is the main "final mile" of the company pipeline.

For each candidate article link it:

1. checks whether the article already exists
2. reuses existing article content when possible
3. otherwise uses the pre-fetched article crawl results
4. enforces recency rules
5. computes article scores
6. saves the article and company link with `add_company_news_article(...)`

Important detail:

This function can work in two modes:

- standalone mode, where it fetches article pages itself
- batch mode, where pre-fetched article results are passed in

The current company pipeline uses the batch mode so article crawling stays consolidated.

## Industry Pipeline Flow

The main entrypoint is:

- `get_all_industry_news()`

This lives in [pipelines/industryNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/industryNewsPipeline.py).

The industry pipeline is similar to the company pipeline, but it is not identical.

### What is the same

It still:

- builds structured jobs
- crawls source pages
- extracts candidate links
- filters candidate links by relevance
- follows article pages
- scores and saves articles

### What is different

The industry pipeline currently does not have the Yahoo news integration that the company pipeline has.

It also still follows article pages more locally inside `_save_followed_article_links(...)` for each batch of candidate links, instead of doing the newer "one combined article crawl batch" approach that the company pipeline now uses.

So if you are comparing the two pipelines and feeling like they are similar-but-not-the-same, that is because they really are in slightly different evolutionary stages.

### Industry matching logic

The industry pipeline uses `_build_industry_match_variants(...)` and `_filter_industry_candidate_links(...)`.

It looks for:

- industry name
- industry key
- simplified token combinations

It also uses the CNBC blacklist helper from `_internal.py`.

## Macro Pipeline Flow

[pipelines/macroNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/macroNewsPipeline.py) is a separate workflow.

It is not the same "search page -> candidate links -> article follow" pattern.

Instead, it parses a structured macro/economic source and stores event-like records rather than normal article records.

So if you are trying to understand the article scraping architecture, the company and industry pipelines are the right starting point.

## Scraping Engine

[engine/article_scraper.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_scraper.py) is the crawling engine.

It provides two public helpers:

- `crawl_articles(urls)`
- `crawl_article_pages(urls)`

### `crawl_articles(urls)`

This is used for source-page crawling.

It runs the Scrapy spider in-process and supports keyboard stop behavior.

### `crawl_article_pages(urls)`

This is used for article-page crawling.

It runs the crawl in a subprocess and then maps results back into:

```python
dict[str, ArticleExtractionResult]
```

The subprocess exists mainly to avoid Twisted reactor restart problems when article crawling needs to happen after other crawler activity.

### `ArticleSpider`

The same Scrapy spider is used for both source-page crawling and article-page crawling.

For each response it:

1. runs `extract_from_response(response)`
2. collects `links` from the page
3. emits one normalized item

That means even source pages go through the same normalization layer, but the pipeline mostly cares about `links` on those pages.

## Extraction Layer

[engine/article_extraction.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_extraction.py) is responsible for turning an HTML response into:

```python
ArticleExtractionResult(
    url=...,
    title=...,
    text=...,
    published_at=...,
    success=...,
    error=...,
)
```

The extraction order is:

1. try a site-specific extractor based on the domain
2. if that fails, try a generic title plus paragraph fallback

Current site-specific extractors include:

- MarketWatch
- Barrons
- CNBC
- Investing
- Fool
- Yahoo

Those extractor modules live under [extractors/](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/extractors).

## Source-Specific Search Extractors

The crawler also has dedicated logic for some search/listing pages.

Current search/listing extractors include:

- MarketWatch search
- CNBC search
- Investing search
- Fool quote pages

These help produce cleaner candidate links than a fully generic "grab every anchor tag" approach.

## Source Configuration

The allowed-source and source-metadata rules come from:

- [source_config.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/source_config.py)
- [config/source.json](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/config/source.json)

This configuration controls things like:

- whether a source is allowed at all
- whether it supports `search` or `listing`
- URL patterns that look like articles
- source reputation values
- max-article-age overrides

This config is central to understanding why some URLs are kept and others are dropped.

## Database Write Path

For the news pipeline, the main database concepts are:

- `news_articles`
- `company_news_articles`
- `industry_news_articles`
- `failed_urls`

The company pipeline writes through:

- `add_company_news_article(...)`

The industry pipeline writes through:

- `add_industry_news_article(...)`

Failed fetches or extraction failures are written to `failed_urls` so later runs can avoid repeatedly hitting known-bad URLs.

## Logging

Logs are written under:

- `Stock-trading-experiment/Data/logs/`

The main logging helper is:

- [core/scrape_logging.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/scrape_logging.py)

In practice, the logs show:

- which source-page crawl started
- which article-page crawl started
- which links were filtered out
- which URLs failed
- which articles were reused from the database
- which articles were saved

If you are debugging runtime behavior, the logs are usually the fastest way to confirm which phase of the pipeline you are in.

## Yahoo News Helper

[market_data/yFinanceNews.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yFinanceNews.py) is intentionally small.

Its job is:

1. ask `yfinance` for company news items
2. reduce the raw Yahoo payload to `(title, url)` pairs
3. keep only URLs from sources your scraper already supports

So the Yahoo helper is not doing extraction, scoring, or saving by itself.

It is only feeding extra article URLs into the company pipeline.

## Yahoo Market Data Side

The market-data system lives nearby but is separate from the article pipeline.

Important files include:

- [market_data/yFinanceData.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yFinanceData.py)
- [market_data/yfinance_client.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yfinance_client.py)
- [market_data/yFinanceRealTime.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yFinanceRealTime.py)

That code is responsible for:

- sector and industry ingestion
- company ingestion
- ranking snapshots
- price snapshots
- throttling and retrying Yahoo requests

This is a separate database path from the article/news scraper.

## Current Differences To Keep In Mind

If the code feels a little uneven, that is a fair read. Right now:

- the company pipeline is the most recently reworked pipeline
- the company pipeline uses explicit jobs and a shared article crawl batch
- the industry pipeline still follows the older pattern in some places
- the top-level wrappers still exist for compatibility
- Yahoo news is only a discovery input, not a first-class standalone scraping pipeline

That means "the pipeline" is not one perfectly uniform thing yet. It is a shared architecture with a few paths at different levels of refactor maturity.

## Quick Trace Example: `get_company_news(...)`

If you want one concrete path to keep in your head, this is the current company flow:

1. resolve the company from the database
2. build search jobs from configured sources
3. fetch Yahoo news items and convert allowed title/URL pairs into direct article jobs
4. crawl all search pages together
5. extract links from those pages
6. filter the candidate links for company relevance
7. combine those filtered links with the Yahoo direct article jobs
8. crawl all article URLs together in one article-page batch
9. reuse existing articles when possible
10. score and save the surviving articles

## Suggested Reading Order

If you want to understand the system with the least confusion, read the files in this order:

1. [pipelines/companyNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/companyNewsPipeline.py)
2. [pipelines/job_builder.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/job_builder.py)
3. [pipelines/_shared.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_shared.py)
4. [pipelines/_internal.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_internal.py)
5. [engine/article_scraper.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_scraper.py)
6. [engine/article_extraction.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/engine/article_extraction.py)
7. [pipelines/industryNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/industryNewsPipeline.py)

That order gives you the current "happy path" first, then the shared machinery, then the older parallel pipeline.
