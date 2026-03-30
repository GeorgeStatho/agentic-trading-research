# Pipelines Folder Guide

This folder is the workflow layer for the news-scraping system.

If `sources/` defines where to start, `engine/` defines how pages are fetched, and `extractors/` defines how article pages are parsed, then `pipelines/` defines how all of those pieces are combined into end-to-end jobs that save records to the database.

## What This Folder Does

The code in this folder is responsible for:

- turning companies, industries, and sectors into scrape jobs
- deciding whether a job is a search-page crawl or a direct article crawl
- filtering discovered links for relevance
- batching article-page fetches
- reusing already-saved articles when possible
- computing article scores and writing final records to the DB

## Current Architecture

The pipeline code is now split into layers:

- entity pipelines
  - `companyNewsPipeline.py`
  - `industryNewsPipeline.py`
  - `sectorNewsPipeline.py`
- shared article-follow mechanics
  - `_article_follow.py`
- shared orchestration
  - `_orchestration.py`
- shared entity adapters
  - `_entity_adapters.py`
- shared setup/constants/helpers
  - `_shared.py`
  - `_constants.py`
  - `_internal.py`
  - `job_builder.py`

The important design change is that the front-facing pipeline files are no longer supposed to own the whole workflow themselves. They mostly:

- find entity records
- build jobs
- define entity-specific relevance rules
- call the shared orchestration layer
- print summary output

## Data Flow

The common flow now looks like this:

1. A public entrypoint such as `get_company_news(...)` or `get_all_industry_news()` loads entity records from the DB.
2. `job_builder.py` converts those entities into structured jobs.
3. The pipeline splits those jobs into:
   - search-style jobs, where a source page must be crawled first
   - direct article jobs, where the article URL is already known
4. `_orchestration.py` runs the search-page crawl batch with `crawl_articles(...)`.
5. The pipeline-specific candidate-link filter decides which links from each page are worth following.
6. `_orchestration.py` converts those links into normalized `ArticleSaveRequest` objects.
7. `_orchestration.py` combines discovered article links and direct article jobs into one article URL batch.
8. `_article_follow.py` reuses existing articles when possible, fetches missing article pages, scores them, and saves them through the entity-specific DB callback.

That means the two-stage crawl behavior is now shared:

- stage 1: crawl source/search/listing pages
- stage 2: crawl article pages

## Files

### [companyNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/companyNewsPipeline.py)

This is the company news entrypoint layer.

Public entrypoints:

- `get_company_news(company_identifier)`
- `get_all_company_news()`

What it is responsible for:

- resolving company records from the DB
- building company search jobs from configured sources
- adding Yahoo-discovered direct article jobs
- defining company-specific relevance matching
- calling the shared orchestration runner

Important internal pieces:

- `_build_company_match_variants(...)`
  - builds normalized company-name and ticker variants used for relevance checks
- `_filter_company_candidate_links(...)`
  - starts from the shared link filter, then keeps only links that actually look related to the company
- `_build_company_jobs(...)`
  - combines configured source jobs with Yahoo-derived direct article jobs
- `_build_all_company_jobs(...)`
  - flattens per-company jobs into one global batch
- `_process_company_jobs(...)`
  - splits jobs into `search` and `article`, then hands them to `run_mixed_job_orchestration(...)`

What is no longer owned directly by this file:

- article fetch/reuse/save mechanics
- search-page to save-request normalization
- direct-article request normalization
- article batch execution

Those moved into `_article_follow.py`, `_orchestration.py`, and `_entity_adapters.py`.

### [industryNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/industryNewsPipeline.py)

This is the industry news entrypoint layer.

Public entrypoints:

- `get_all_industry_news()`
- `get_industry_news(industry_identifier)`

What it is responsible for:

- resolving industry records
- building industry jobs from configured listing and search sources
- defining industry-specific relevance matching
- calling the shared orchestration runner
- reporting saved totals separately for `listing` and `search`

Important internal pieces:

- `_build_industry_match_variants(...)`
  - builds practical industry match terms from the industry name and key
- `_filter_industry_candidate_links(...)`
  - applies shared structural filtering, then industry-specific relevance matching
- `_find_industry(...)`
  - resolves one industry by key or exact name

Why this pipeline still looks a little larger than the company pipeline:

- it supports both `listing` and `search` jobs
- it keeps separate saved-count buckets for those two source types
- that extra reporting behavior is entity-specific even though the crawl flow is shared

Even so, the actual crawl orchestration is now shared through `run_mixed_job_orchestration(...)`.

### [sectorNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/sectorNewsPipeline.py)

This is the sector news entrypoint layer.

Public entrypoint:

- `get_sector_news(sector_identifier, urls, source_page_url="https://www.cnbc.com/")`

What it is responsible for:

- resolving one sector from the DB
- taking a provided list of article URLs
- constraining that list to CNBC-style sector crawling
- sending those direct article jobs through the shared article-save runner

This pipeline is simpler because it does not start from source-page discovery. It receives article URLs directly and uses `run_article_save_requests(...)`.

### [_article_follow.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_article_follow.py)

This module contains the low-level article follow/save mechanics shared by all entity pipelines.

Key functions:

- `collect_article_urls_to_fetch(...)`
- `save_followed_article_links(...)`

What `collect_article_urls_to_fetch(...)` does:

- walks a list of candidate links
- skips empty URLs
- skips disallowed sources
- skips URLs already in the DB
- skips duplicates
- respects the per-request article limit
- returns only the URLs that still need fetching

What `save_followed_article_links(...)` does:

- receives normalized candidate links for one entity and one source-page context
- reuses an existing DB article if one is already saved
- otherwise reads the fetched `ArticleExtractionResult`
- applies recency checks
- computes normalization fields and scoring data
- builds a shared context dictionary
- hands that context to an entity-specific save callback

This module is intentionally generic. It does not know whether it is saving company, industry, or sector articles. The caller provides:

- the entity label
- the DB save callback
- the raw JSON builder
- optional link inclusion rules

### [_orchestration.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_orchestration.py)

This module contains the shared workflow runner for turning jobs into saved articles.

Key types and functions:

- `ArticleSaveRequest`
- `build_search_article_save_requests(...)`
- `build_direct_article_save_requests(...)`
- `collect_save_request_article_urls(...)`
- `run_article_save_requests(...)`
- `run_mixed_job_orchestration(...)`

`ArticleSaveRequest` is the normalized handoff shape between discovery and saving. Each request contains:

- `source_page_url`
- `candidate_links`
- `entity`
- `entity_id`
- `max_articles`
- `payload`

What each helper does:

- `build_search_article_save_requests(...)`
  - turns crawled source/search/listing pages into normalized save requests
- `build_direct_article_save_requests(...)`
  - turns known article URLs into the same save-request shape
- `collect_save_request_article_urls(...)`
  - computes the combined article URL batch across many save requests
- `run_article_save_requests(...)`
  - fetches the combined article batch once, then replays each save request against those fetched results
- `run_mixed_job_orchestration(...)`
  - handles the common two-pass pipeline:
    - crawl search pages first
    - build save requests
    - combine them with direct article requests
    - run one article crawl/save pass

This is the main reason the pipelines are easier to refactor now. The crawl flow lives here instead of being duplicated in each entity file.

### [_entity_adapters.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_entity_adapters.py)

This module builds the entity-specific adapter functions that plug company, industry, and sector logic into the shared orchestration layer.

Key functions:

- `make_entity_article_saver(...)`
- `make_search_request_builder(...)`
- `make_direct_request_builder(...)`
- `make_request_saver(...)`
- `make_bucketed_count_accumulator(...)`

What these adapters do:

- `make_entity_article_saver(...)`
  - wraps `save_followed_article_links(...)` with entity-specific DB save logic and raw JSON structure
- `make_search_request_builder(...)`
  - creates a helper that converts crawled source pages into save requests for one entity type
- `make_direct_request_builder(...)`
  - creates a helper that converts direct article jobs into save requests
- `make_request_saver(...)`
  - creates the callback that `_orchestration.py` uses when replaying save requests
- `make_bucketed_count_accumulator(...)`
  - lets a pipeline keep separate counters, such as industry `listing` vs `search`

This module is what keeps the pipelines thin without hiding entity-specific behavior. The entity file still decides what makes a link relevant and what payload should be saved, but the repeated plumbing is centralized here.

### [job_builder.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/job_builder.py)

This module creates structured jobs instead of having each pipeline build source URLs inline.

Important helpers:

- `build_company_source_jobs(...)`
- `build_industry_source_jobs(...)`
- `build_company_source_job(...)`
- `build_yahoo_news_jobs(...)`
- `group_jobs_by_url(...)`
- `unique_job_urls(...)`

What these functions do:

- convert entity records plus source configuration into normalized job dicts
- validate whether a URL makes sense for a source type
- support both configured source-page jobs and Yahoo-derived direct article jobs
- provide batching helpers used by `_orchestration.py`

This keeps the pipeline files from having to know how URLs are assembled.

### [_shared.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_shared.py)

This module is the compatibility and convenience layer that most pipelines import from.

It pulls together:

- crawl functions from `engine/`
- scoring and normalization helpers
- DB lookups for existing articles and failed URLs
- shared configuration values such as max article counts and age limits
- logger setup

The goal is not business logic. The goal is to give the pipeline layer one stable import surface for common dependencies.

### [_internal.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_internal.py)

This module contains shared pipeline helper logic that is internal to the pipeline layer.

Examples include:

- URL blacklists
- normalized text matching
- helper functions that decide whether a link text or href matches a target entity

This is where small reusable pieces live when they are too pipeline-specific for `_shared.py` but too common to duplicate in every entity pipeline.

### [_constants.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_constants.py)

This module contains pipeline-specific constants such as:

- company name suffixes
- industry stopwords

These constants are used by the entity match-variant builders.

### [macroNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/macroNewsPipeline.py)

This is the macro/economic data pipeline.

It is separate from the company, industry, and sector article pipelines and should be thought of as its own workflow.

## How To Read The Pipeline Code

If you are trying to understand the system quickly, the best order is:

1. `job_builder.py`
2. `_orchestration.py`
3. `_article_follow.py`
4. `_entity_adapters.py`
5. `companyNewsPipeline.py`
6. `industryNewsPipeline.py`
7. `sectorNewsPipeline.py`

That order usually makes the refactored structure click faster than starting from one of the public pipeline entrypoints.
