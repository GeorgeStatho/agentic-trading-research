# Pipelines Folder Guide

This folder is the workflow layer for the news-scraping system.

If `sources/` defines where to start, `engine/` defines how pages are fetched, and `extractors/` defines how article pages are parsed, then `pipelines/` defines how all of those pieces are combined into end-to-end jobs that save records to the database.

## What This Folder Does

The code in this folder is responsible for:

- turning companies, industries, and sectors into scrape jobs
- deciding whether a job is a search-page crawl or a direct article crawl
- filtering discovered links for relevance
- batching article-page fetches
- linking already-saved articles to the entity instead of saving them again
- computing article scores and writing final records to the DB

## Current Architecture

The pipeline code is now split into layers:

- entity pipelines
  - `companyNewsPipeline.py`
  - `industryNewsPipeline.py`
  - `sectorNewsPipeline.py`
  - `USNewsPipeline.py`
  - `worldNewsPipeline.py`
- shared article-follow mechanics
  - `_article_follow.py`
- shared orchestration
  - `_orchestration.py`
- shared CNBC RSS helpers
  - `_sector_rss.py`
  - `_regionNewsPipeline.py`
- shared setup/constants/helpers
  - `_shared.py`
  - `_constants.py`
  - `_internal.py`
  - `job_builder.py`

The important design change is that the front-facing pipeline files are no longer supposed to own the whole workflow themselves. They mostly:

- find entity records
- build jobs
- define entity-specific relevance rules
- describe the entity as a `NewsEntity` (what it is, its identity fields, and which link-table function connects it to an article)
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
6. `_orchestration.py` converts those links into normalized `ArticleSaveRequest` objects, and turns direct article jobs into the same shape with `direct_article_save_request(...)`.
7. `run_article_save_requests(...)` combines every request's links into one article URL batch (skipping disallowed, stale, already-saved and duplicate URLs) and fetches that batch once.
8. `save_followed_article_links(...)` in `_article_follow.py` then replays each request against the fetched results. For each link:
   - an article already in `news_articles` only gets a new row in the entity's link table
   - a new article is scored, inserted into `news_articles`, and linked to the entity in the same transaction

That means the two-stage crawl behavior is shared by every pipeline:

- stage 1: crawl source/search/listing pages (skipped when every job is already a direct article)
- stage 2: fetch article pages

There is also a simpler RSS-first flow used by the sector, U.S., and world pipelines. It has no stage 1:

1. fetch a CNBC RSS feed
2. extract recent `(article URL, description)` pairs
3. wrap each pair in a direct article save request, with the description as the summary
4. run one shared fetch/save batch through `run_article_save_requests(...)`

Already-saved articles are not classified up front. They are detected inside `save_followed_article_links(...)`, which links them without re-fetching or re-scoring.

## How Articles Are Saved

The same rules apply to every pipeline:

- `news_articles` is shared. An article that is already saved is never rewritten. Another entity that finds it only adds a row to its own link table.
- New articles get `summary` from the discovered link text: the anchor text for links found on search/listing pages, the RSS `description` for sector, U.S., and world articles, and `None` for direct article jobs that carry no text (such as the API and Yahoo ones). The entity name is never used as a summary.
- `raw_json` holds the `NewsEntity.identity` fields plus a common tail (source page URL, link, normalized URL/title, content hash, source metadata, scores, extracted article).
- Older rows may still carry an entity name such as `World News` as their summary. [../../../scripts/clear_placeholder_summaries.py](../../../scripts/clear_placeholder_summaries.py) is the one-off backfill that sets those to `NULL`. It is a dry run unless you pass `--apply`.
- In production, `Python Scripts/agentCallers/agent_pipeline/news_refresh.py` runs these entry points one entity per subprocess: `get_us_news_from_rss`, `get_world_news_from_rss`, `get_sector_news_from_rss`, `get_industry_news`, and `get_company_news`.

## Files

### [companyNewsPipeline.py](companyNewsPipeline.py)

This is the company news entrypoint layer.

Public entrypoints:

- `get_company_news(company_identifier)`
- `get_all_company_news()`

What it is responsible for:

- resolving company records from the DB
- building company search jobs from configured sources
- adding direct article jobs discovered through the Fool, Morningstar, GuruFocus, and Yahoo APIs
- defining company-specific relevance matching
- calling the shared orchestration runner

Important internal pieces:

- `_company_entity(...)`
  - builds the company's `NewsEntity` (kind `company`, linked with `link_company_to_article`)
- `_filter_company_candidate_links(...)`
  - applies the shared link filter and the CNBC blacklist; it does not check the link text for a company-name match
- `_build_api_jobs(...)`
  - turns Fool, Morningstar, and GuruFocus API results into direct `article` jobs
- `_build_company_jobs(...)`
  - combines configured source jobs with the API-derived and Yahoo-derived direct article jobs
- `_build_all_company_jobs(...)`
  - flattens per-company jobs into one global batch
- `_process_company_jobs(...)`
  - splits jobs into `search` and `article`, then hands them to `run_mixed_job_orchestration(...)`

What is no longer owned directly by this file:

- article fetch/reuse/save mechanics
- search-page to save-request normalization
- direct-article request normalization
- article batch execution

Those live in `_article_follow.py` and `_orchestration.py`.

### [industryNewsPipeline.py](industryNewsPipeline.py)

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
- `_industry_entity(...)`
  - builds the industry's `NewsEntity` (kind `industry`, linked with `link_industry_to_article`)
- `_find_industry(...)`
  - resolves one industry by key or exact name

Why this pipeline still looks a little larger than the company pipeline:

- it supports both `listing` and `search` jobs
- it keeps separate saved-count buckets for those two source types
- that extra reporting behavior is entity-specific even though the crawl flow is shared

Even so, the actual crawl orchestration is now shared through `run_mixed_job_orchestration(...)`.

### [sectorNewsPipeline.py](sectorNewsPipeline.py)

This is the sector news entrypoint layer.

Public entrypoint:

- `get_sector_news(sector_identifier, urls, source_page_url="https://www.cnbc.com/")`
- `get_sector_news_from_rss(sector_identifier, max_age_days=...)`
- `get_all_sector_news(max_age_days=...)`

What it is responsible for:

- resolving one sector from the DB
- loading the mapped CNBC RSS feed URLs for that sector
- extracting recent `(URL, description)` items from those feeds
- sending those items through the shared article-save runner as direct article requests, with the RSS description as the summary

Important internal pieces:

- `_sector_entity(...)`
  - builds the sector's `NewsEntity` (kind `sector`, linked with `link_sector_to_article`)
- `_get_sector_rss_jobs(...)`
  - collects the recent RSS items for one sector and wraps them with `build_sector_rss_jobs(...)`
- `_sector_save_requests(...)`
  - turns sector jobs into `direct_article_save_request(...)` objects
- `_run_sector_jobs(...)`
  - saves one sector's requests and prints the totals

This pipeline is RSS-first. It does not crawl search pages. `get_all_sector_news(...)` collects the requests for every sector and saves them in one `run_article_save_requests(...)` batch, so an article shared by several sectors is fetched once. `get_sector_news_from_rss(...)` does the same for a single sector.

### [USNewsPipeline.py](USNewsPipeline.py)

This is the U.S. macro/business news pipeline.

Public entrypoints:

- `get_us_news(urls)`
- `get_us_news_from_rss(max_age_days=...)`

What it is responsible for:

- using the fixed CNBC U.S. News RSS feed
- extracting recent CNBC articles with their RSS descriptions
- saving new articles, or just linking already-saved ones, into the `us_news_articles` table

This pipeline is intentionally thin. Most of its behavior is delegated into `_regionNewsPipeline.py`.

### [worldNewsPipeline.py](worldNewsPipeline.py)

This is the world macro/business news pipeline.

Public entrypoints:

- `get_world_news(urls)`
- `get_world_news_from_rss(max_age_days=...)`

What it is responsible for:

- using the fixed CNBC World News RSS feed
- extracting recent CNBC articles with their RSS descriptions
- saving new articles, or just linking already-saved ones, into the `world_news_articles` table

Like the U.S. pipeline, it is intentionally thin and delegates most of its behavior into `_regionNewsPipeline.py`.

### [_article_follow.py](_article_follow.py)

This module contains the low-level article follow/save mechanics shared by all entity pipelines.

Key pieces:

- `NewsEntity`
- `collect_article_urls_to_fetch(...)`
- `fetch_articles_with_preferred_extractors(...)`
- `save_followed_article_links(...)`

`NewsEntity` is the one description of "who is this article for" that every pipeline passes in:

- `kind`: `company`, `industry`, `sector`, `us`, or `world`
- `id` and `label`: used for counting and logging
- `identity`: the entity fields written at the front of `raw_json`
- `link`: the `news_db_modules.links` function that connects an article to this entity, called as `link(article_id=, source_page_url=, conn=)`

What `collect_article_urls_to_fetch(...)` does:

- walks a list of candidate links
- skips empty URLs
- skips links rejected by the optional `should_include_link` rule
- skips disallowed sources
- skips CNBC URLs whose dated path is already too old
- skips URLs already in the DB
- skips duplicates
- respects the per-request article limit
- returns only the URLs that still need fetching

What `fetch_articles_with_preferred_extractors(...)` does:

- fetches `fool.com`, `investing.com`, `morningstar.com`, and `gurufocus.com` URLs through their `APIs/` article-content functions
- sends every other URL, and any API failure, to `crawl_article_pages(...)`

What `save_followed_article_links(...)` does, per candidate link:

- skips links that fail the allowlist or the recency check
- for an article that is already saved, links it to the entity through `entity.link` and leaves `news_articles` untouched
- for a new article, reads the fetched `ArticleExtractionResult`, computes scores, and calls `add_news_article(...)` plus `entity.link` in one transaction
- stores the link text as `summary` and `NewsEntity.identity` plus the common tail as `raw_json`
- records failed fetches in `failed_urls`

This module is intentionally generic. It does not know whether it is saving company, industry, or sector articles. The caller provides the `NewsEntity` and, optionally, a link inclusion rule.

### [_orchestration.py](_orchestration.py)

This module contains the shared workflow runner for turning jobs into saved articles.

Key types and functions:

- `ArticleSaveRequest`
- `direct_article_save_request(...)`
- `build_search_article_save_requests(...)`
- `collect_save_request_article_urls(...)`
- `run_article_save_requests(...)`
- `run_mixed_job_orchestration(...)`

`ArticleSaveRequest` is the normalized handoff shape between discovery and saving. Each request contains:

- `source_page_url`
- `candidate_links`
- `entity` (a `NewsEntity`)
- `max_articles`
- `source_type`

What each helper does:

- `direct_article_save_request(url, entity, summary=None)`
  - turns one known article URL into a save request; `summary` becomes the link text
- `build_search_article_save_requests(...)`
  - turns crawled source/search/listing pages into save requests, using the pipeline's `entity_from_job` and `candidate_links_for` callbacks
- `collect_save_request_article_urls(...)`
  - computes the combined article URL batch across many save requests
- `run_article_save_requests(...)`
  - fetches the combined article batch once, then replays each save request through `save_followed_article_links(...)`; counts are keyed by entity id, or by `(entity id, source_type)` with `by_source_type=True`
- `run_mixed_job_orchestration(...)`
  - handles the common two-pass pipeline:
    - crawl search pages first
    - build save requests
    - combine them with direct article requests
    - run one article fetch/save pass

This is the main reason the pipelines are easier to refactor now. The crawl flow lives here instead of being duplicated in each entity file, and entity-specific behavior reaches it only through a `NewsEntity` and a candidate-links callback.

### [_sector_rss.py](_sector_rss.py)

This module contains CNBC RSS-specific helpers used by the sector pipeline.

Key functions:

- `load_sector_rss_feed_map()`
- `get_sector_feed_urls(...)`
- `fetch_rss_feed_xml(...)`
- `extract_recent_rss_items(...)`
- `get_recent_sector_feed_items(...)`

What it does:

- loads the sector-to-RSS-feed mapping from `Data/sector_rss_feeds.json`
- resolves which RSS feeds belong to a sector
- fetches RSS XML from CNBC
- extracts only `(article URL, description)` items newer than the configured age window

This keeps RSS parsing and feed resolution out of the sector pipeline entrypoint file.

### [_regionNewsPipeline.py](_regionNewsPipeline.py)

This module is a small shared base for fixed-feed CNBC region pipelines such as U.S. News and World News.

`make_region_pipeline(...)` takes a region slug, label, RSS feed URL, and link function, builds the region's `NewsEntity`, and returns `get_news` and `get_news_from_rss` functions. They handle:

- fixed RSS feed fetching
- recent `(URL, description)` extraction
- direct article save requests, with the RSS description as the summary
- one shared article fetch/save batch, limited to CNBC URLs

The U.S. and world pipeline files are mostly just configuration wrappers around this module.

### [job_builder.py](job_builder.py)

This module creates structured jobs instead of having each pipeline build source URLs inline.

Important helpers:

- `build_company_source_jobs(...)`
- `build_industry_source_jobs(...)`
- `build_company_source_job(...)`
- `build_yahoo_news_jobs(...)`
- `build_sector_rss_jobs(...)`
- `group_jobs_by_url(...)`
- `unique_job_urls(...)`

What these functions do:

- convert entity records plus source configuration into normalized job dicts
- validate whether a URL makes sense for a source type
- support both configured source-page jobs and Yahoo-derived direct article jobs
- support RSS-derived direct article jobs for sector scraping
- provide batching helpers used by `_orchestration.py`

This keeps the pipeline files from having to know how URLs are assembled.

### [_shared.py](_shared.py)

This module is the compatibility and convenience layer that most pipelines import from.

It pulls together:

- crawl functions from `engine/`
- scoring and normalization helpers
- DB lookups for existing articles and failed URLs
- shared configuration values such as max article counts and age limits
- logger setup

The goal is not business logic. The goal is to give the pipeline layer one stable import surface for common dependencies.

### [_internal.py](_internal.py)

This module contains shared pipeline helper logic that is internal to the pipeline layer.

Examples include:

- URL blacklists
- normalized text matching
- helper functions that decide whether a link text or href matches a target entity

This is where small reusable pieces live when they are too pipeline-specific for `_shared.py` but too common to duplicate in every entity pipeline.

### [_constants.py](_constants.py)

This module contains pipeline-specific constants such as:

- industry stopwords, used by `_build_industry_match_variants(...)`
- Fool exchange slugs, used by `job_builder.py`

It also still holds a `COMPANY_NAME_SUFFIXES` set that nothing imports now.

### [macroNewsPipeline.py](macroNewsPipeline.py)

This is the macro/economic data pipeline.

It is separate from the company, industry, and sector article pipelines and should be thought of as its own workflow.

## How To Read The Pipeline Code

If you are trying to understand the system quickly, the best order is:

1. `job_builder.py`
2. `_orchestration.py`
3. `_article_follow.py`
4. `_sector_rss.py`
5. `_regionNewsPipeline.py`
6. `companyNewsPipeline.py`
7. `industryNewsPipeline.py`
8. `sectorNewsPipeline.py`
9. `USNewsPipeline.py`
10. `worldNewsPipeline.py`

That order usually makes the refactored structure click faster than starting from one of the public pipeline entrypoints.
