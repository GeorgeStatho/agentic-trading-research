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
4. `_orchestration.py` runs the search-page batch with `fetch_source_pages(...)` from [../engine/page_fetch.py](../engine/page_fetch.py).
5. The pipeline-specific candidate-link builder returns the links from each page (company: all of them; industry: only the ones that match the industry).
6. `_orchestration.py` converts those links into normalized `ArticleSaveRequest` objects, and turns direct article jobs into the same shape with `direct_article_save_request(...)`.
7. `run_article_save_requests(...)` gives every link one verdict with `judge_links(...)` in `_link_policy.py` (one batched DB lookup for the whole run), then fetches the unique `fetch` URLs once with `fetch_articles(...)` (also `page_fetch`).
8. `save_link_verdicts(...)` in `_article_follow.py` then replays each request's verdicts against the fetched results:
   - a `reuse` verdict (an article already in `news_articles`) only gets a new row in the entity's link table
   - a `fetch` verdict is scored, inserted into `news_articles`, and linked to the entity in the same transaction; a URL shared by several requests is inserted once and linked for each

That means the two-stage crawl behavior is shared by every pipeline:

- stage 1: crawl source/search/listing pages (skipped when every job is already a direct article)
- stage 2: fetch article pages

There is also a simpler RSS-first flow used by the sector, U.S., and world pipelines. It has no stage 1:

1. fetch a CNBC RSS feed with `fetch_feed(...)` from `page_fetch`
2. extract recent `(article URL, description)` pairs
3. wrap each pair in a direct article save request, with the description as the summary
4. run one shared fetch/save batch through `run_article_save_requests(...)`

Pipelines never crawl or open URLs themselves; every fetch goes through [../engine/page_fetch.py](../engine/page_fetch.py). It contains failures: a crashed backend leaves the affected URLs missing from the result, and a missing page, article or feed is skipped and only logged. Only a page that was fetched but extracted with `success=False` is recorded in `failed_urls`. So an outage shows up as "saved 0" plus warnings in the logs, and the same URLs are tried again on the next run. See [../engine/README.md](../engine/README.md).

Every link is classified up front, before anything is fetched, by `judge_links(...)` in `_link_policy.py`:

- `fetch`: a new article to download, up to `max_articles` per request (10 per search page, 1 per direct job)
- `reuse`: an article already saved and recent; it is only linked, never re-fetched or re-scored, and it does not count toward the limit (saved counts include it)
- `skip`, with a reason: `no_href`, `duplicate` (same normalized URL earlier in the same request), `excluded` (the caller's `should_include_link` said no), `disallowed_source`, `not_article_url` (matches none of the source's article patterns), `blacklisted`, `stale_url_date`, `stale_existing`, `irrelevant_existing`, `failed_url` or `limit`

The checks run in that order. One `get_saved_and_failed_urls(...)` call per run looks up all surviving URLs. A URL that failed 3 times, or permanently, is skipped on every path (search pages, listing pages and direct jobs).

Recency has three steps: the date in a CNBC URL and the stored date of an existing article are checked in `judge_links(...)` against the per-source `max_article_age_days` (default 14 days); the extracted publish date is checked in `save_link_verdicts(...)` after the fetch. The RSS 3-day window (`DEFAULT_RSS_MAX_AGE_DAYS`) only decides which feed items are discovered.

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
- `_company_candidate_links(...)`
  - returns every link of a supported search page; it does not check the link text for a company-name match
- `_company_reuse_filter(...)`
  - builds the `may_reuse` rule for search pages: an already-saved article is linked only if the company name (also without corporate suffixes such as Inc) or a ticker of 3+ characters appears as whole words in the link text or URL
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
  - keeps only the links whose text or URL matches an industry variant
- `_listing_topic_links(...)`
  - keeps listing-page links whose text or URL contains the industry name
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
- `save_link_verdicts(...)`

`NewsEntity` is the one description of "who is this article for" that every pipeline passes in:

- `kind`: `company`, `industry`, `sector`, `us`, or `world`
- `id` and `label`: used for counting and logging
- `identity`: the entity fields written at the front of `raw_json`
- `link`: the `news_db_modules.links` function that connects an article to this entity, called as `link(article_id=, source_page_url=, conn=)`

This module does not fetch anything and does not decide which links to follow; that is `_link_policy.py`. Choosing between a site API and the crawler for an article URL now lives in `fetch_articles(...)` in [../engine/page_fetch.py](../engine/page_fetch.py).

What `save_link_verdicts(...)` does, per verdict (it ignores `skip` verdicts except to log them):

- for a `reuse` verdict, links the saved article to the entity through `entity.link` and leaves `news_articles` untouched
- for a `fetch` verdict whose URL was already handled earlier in the run (the shared `outcomes` map), only links the same article, or skips it if that attempt was rejected; the stored row and `raw_json` are never overwritten
- for a first-seen `fetch` verdict, reads the fetched `ArticleExtractionResult`, checks the extracted date with the verdict's `max_age_days`, computes scores, and calls `add_news_article(...)` plus `entity.link` in one transaction
- stores the link text as `summary` and `NewsEntity.identity` plus the common tail as `raw_json`
- records fetched-but-failed extractions in `failed_urls`; a URL that is missing from the fetched batch (backend outage) is only logged, not recorded

This module is intentionally generic. It does not know whether it is saving company, industry, or sector articles. The caller provides the `NewsEntity` and the verdicts.

### [_link_policy.py](_link_policy.py)

This module gives every candidate link one verdict before anything is fetched.

Key pieces:

- `LinkVerdict`: the link, its stripped `href` and `normalized_url`, the `action` (`fetch`, `reuse` or `skip`), the skip `reason`, the per-source `max_age_days`, and the `article_id` of a reused article
- `LinkBatch`: the links of one save request, its `max_articles` cap on `fetch` verdicts, and an optional `may_reuse` rule
- `judge_links(batches, should_include_link=None)`: returns one list of verdicts per batch, in input order

`judge_links(...)` is a pure decision: no logging and no DB writes. It runs the database-free checks for every batch first, then makes exactly one `get_saved_and_failed_urls(...)` call (from `news_db_modules/queries.py`) for the survivors, and only then applies the existing-article, failed-URL and limit rules. The reasons are listed in the data-flow section above. The same URL can be `fetch` in two batches; the orchestration layer fetches it once.

### [_orchestration.py](_orchestration.py)

This module contains the shared workflow runner for turning jobs into saved articles.

Key types and functions:

- `ArticleSaveRequest`
- `direct_article_save_request(...)`
- `build_search_article_save_requests(...)`
- `run_article_save_requests(...)`
- `run_mixed_job_orchestration(...)`

`ArticleSaveRequest` is the normalized handoff shape between discovery and saving. Each request contains:

- `source_page_url`
- `candidate_links`
- `entity` (a `NewsEntity`)
- `max_articles`
- `source_type`
- `may_reuse` (optional rule deciding whether an already-saved article found on a page may be linked; `None` for direct jobs)

What each helper does:

- `direct_article_save_request(url, entity, summary=None)`
  - turns one known article URL into a save request; `summary` becomes the link text
- `build_search_article_save_requests(...)`
  - turns crawled source/search/listing pages into save requests, using the pipeline's `entity_from_job` and `candidate_links_for` callbacks and, optionally, `reuse_filter_for(job)` for `may_reuse`
- `run_article_save_requests(...)`
  - judges all links with `judge_links(...)`, fetches the unique `fetch` URLs once with `fetch_articles(...)`, then replays each save request's verdicts through `save_link_verdicts(...)`; counts are keyed by entity id, or by `(entity id, source_type)` with `by_source_type=True`
- `run_mixed_job_orchestration(...)`
  - handles the common two-pass pipeline:
    - fetch the search pages first with `fetch_source_pages(...)`
    - build save requests
    - combine them with direct article requests
    - run one article fetch/save pass

This is the main reason the pipelines are easier to refactor now. The crawl flow lives here instead of being duplicated in each entity file, and entity-specific behavior reaches it only through a `NewsEntity` and a candidate-links callback.

### [_sector_rss.py](_sector_rss.py)

This module contains CNBC RSS-specific helpers used by the sector pipeline.

Key functions:

- `load_sector_rss_feed_map()`
- `get_sector_feed_urls(...)`
- `extract_recent_rss_items(...)`
- `get_recent_sector_feed_items(...)`

What it does:

- loads the sector-to-RSS-feed mapping from `Data/sector_rss_feeds.json`
- resolves which RSS feeds belong to a sector
- fetches each feed through `fetch_feed(...)` in `page_fetch`
- extracts only `(article URL, description)` items newer than the configured age window

`extract_recent_rss_items(...)` returns `[]` for a missing feed (`None` or empty text) and, with a logged warning, for broken XML. One bad feed therefore does not stop the other feeds of a sector.

This keeps RSS parsing and feed resolution out of the sector pipeline entrypoint file.

### [_regionNewsPipeline.py](_regionNewsPipeline.py)

This module is a small shared base for fixed-feed CNBC region pipelines such as U.S. News and World News.

`make_region_pipeline(...)` takes a region slug, label, RSS feed URL, and link function, builds the region's `NewsEntity`, and returns `get_news` and `get_news_from_rss` functions. They handle:

- fixed RSS feed fetching through `fetch_feed(...)` (a missing feed saves 0 and does not raise)
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

### [_internal.py](_internal.py)

This module contains shared pipeline helper logic that is internal to the pipeline layer.

Examples include:

- the CNBC URL blacklist (applied by `_link_policy.py`)
- normalized text matching
- helper functions that decide whether a link text or href matches a target entity

This is where small reusable pieces live when they are too pipeline-specific for `core/` but too common to duplicate in every entity pipeline.

### [_constants.py](_constants.py)

This module contains pipeline-specific constants such as:

- industry stopwords, used by `_build_industry_match_variants(...)`
- Fool exchange slugs, used by `job_builder.py`

### [macroNewsPipeline.py](macroNewsPipeline.py)

This is the macro/economic data pipeline.

It is separate from the company, industry, and sector article pipelines and should be thought of as its own workflow.

## Testing Pipelines Offline

Because all fetching goes through `page_fetch`, a pipeline can run end to end without the network by swapping the fetcher:

```python
from engine.page_fetch import FixtureFetcher, use_fetcher

fixture = FixtureFetcher(pages={...}, articles={...}, feeds={...})
with use_fetcher(fixture):
    saved = get_us_news_from_rss()
```

`fixture.requested` lists every URL the pipeline asked for. URLs without a fixture come back missing, like a backend outage. See [../../../tests/test_article_follow.py](../../../tests/test_article_follow.py) for examples. Use `tests/db_fixtures.temporary_database(self)` for the database.

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
