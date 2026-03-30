# Pipelines Folder Guide

This folder contains the orchestration layer for the scraping workflows.

If `sources/` defines where to start and `engine/` defines how pages are fetched and parsed, `pipelines/` decides the actual end-to-end workflow.

## What This Folder Does

The code in this folder is responsible for:

- building scrape jobs
- choosing which sources to crawl for companies or industries
- filtering discovered links for relevance
- coordinating source-page and article-page crawling
- reusing already-saved articles when possible
- scoring and saving final article records

## Files

### [companyNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/companyNewsPipeline.py)

This is the main company news pipeline.

Public entrypoints:

- `get_company_news(company_identifier)`
- `get_all_company_news()`

Current behavior:

- loads company records from the DB
- builds normal company source jobs
- adds Yahoo-derived direct article jobs from `market_data/yFinanceNews.py`
- crawls all search/source pages together
- filters candidate links for company relevance
- combines discovered article URLs with Yahoo direct article URLs
- runs one shared article-page crawl batch
- scores and saves articles with `add_company_news_article(...)`

Important internal pieces:

- `_build_company_match_variants(...)`
- `_filter_company_candidate_links(...)`
- `_build_company_jobs(...)`
- `_build_article_save_requests(...)`
- `_collect_article_urls_to_fetch(...)`
- `_save_followed_article_links(...)`

This is the most recently reworked pipeline and the best reference for the current architecture.

#### What the company internal pieces do

`_build_company_match_variants(company)`

- builds a set of normalized terms that represent the company
- starts from the company name
- trims suffixes like `Inc`, `Corp`, and similar variants using `_constants.py`
- adds the company ticker symbol when it is long enough to be meaningful
- returns the set of normalized strings used later for relevance checks

Why it exists:

- search pages often contain many generic market links
- the scraper needs a cheap way to ask "does this link text or URL really look like it belongs to this company?"

`_filter_company_candidate_links(page_url, links, company)`

- starts with the shared `filter_article_links(...)` helper
- removes links from disallowed sources, already-saved articles, failed URLs, and URLs that do not match source article patterns
- then applies company-specific filtering
- skips blacklisted CNBC paths
- keeps only links whose text or URL matches one of the company variants

Why it exists:

- the shared filter answers "is this link structurally allowed?"
- this function answers "is this link actually relevant to this company?"

`_build_company_jobs(company)`

- builds the company's normal search jobs with `build_company_source_jobs([company])`
- fetches Yahoo news items with `get_company_news_items(symbol)`
- reduces those items into filtered `(title, url)` pairs with `extract_title_and_url(...)`
- converts those pairs into direct `article` jobs with `build_yahoo_news_jobs(...)`
- returns one combined job list for that company

Why it exists:

- it centralizes all company-specific discovery sources in one place
- it keeps the rest of the pipeline from caring whether a job came from configured search sources or Yahoo news discovery

`_build_all_company_jobs(companies)`

- loops through every company
- calls `_build_company_jobs(company)` for each one
- flattens all per-company job lists into one large job list

Why it exists:

- `get_all_company_news()` wants one global job set so search crawling can be batched efficiently

`_build_article_save_requests(crawled_pages, jobs_by_url, direct_article_jobs)`

- takes the results from the search-page crawl
- maps each crawled page back to the jobs that produced it
- filters that page's discovered links for the matching company
- converts those filtered links into a normalized "save request" shape
- also converts direct Yahoo article jobs into the same save-request shape

The save-request shape is important because it lets the pipeline treat:

- "article links discovered from a source page"
- and "article URLs given directly by Yahoo"

as the same kind of downstream work.

Why it exists:

- this is the bridge between source-page crawling and article-page crawling
- it normalizes two different discovery paths into one later save path

`_collect_article_urls_to_fetch(save_requests)`

- walks through all save requests
- collects the article URLs that still need network fetches
- skips disallowed sources
- skips URLs already present in the DB
- skips duplicates across the batch
- respects the `max_articles` limit for each request

Why it exists:

- the company pipeline wants to do one shared `crawl_article_pages(...)` call
- this helper computes exactly which URLs belong in that one article batch

`_save_followed_article_links(...)`

- takes a source page URL, candidate links, company record, and optionally pre-fetched article results
- checks whether each candidate article already exists
- reuses stored article content when possible
- otherwise reads the corresponding `ArticleExtractionResult`
- applies recency checks
- computes normalized title, normalized URL, content hash, and score bundle
- writes the final article through `add_company_news_article(...)`

Why it exists:

- this is the last major step in the company pipeline
- it is the point where candidate links become saved article records

Important detail:

- if `fetched_articles` is passed in, it behaves as part of a shared crawl batch
- if not, it can still fetch article pages on its own
- the current company pipeline uses it in the shared-batch mode

`_process_company_jobs(jobs)`

- splits the mixed job list into `search` jobs and direct `article` jobs
- crawls search jobs first with `crawl_articles(...)`
- builds save requests from:
  - search-page-discovered article links
  - Yahoo direct article jobs
- collects one combined article URL batch
- runs one `crawl_article_pages(...)` call
- feeds the results into `_save_followed_article_links(...)`

Why it exists:

- this is the main orchestration function of the company pipeline
- most of the "two-stage crawl" behavior is easiest to understand by reading this function

### [industryNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/industryNewsPipeline.py)

This is the industry news pipeline.

Public entrypoint:

- `get_all_industry_news()`

Current behavior:

- loads industries from the DB
- builds industry source jobs
- crawls source pages
- filters candidate links for industry relevance
- follows article links
- scores and saves articles with `add_industry_news_article(...)`

Important note:

- it is similar to the company pipeline
- but it still follows the older pattern where article fetches happen more locally instead of the newer shared article-crawl batching pattern

#### What the industry internal pieces do

`_build_industry_match_variants(industry)`

- normalizes the industry name
- removes common stopwords using `INDUSTRY_NAME_STOPWORDS`
- builds a few compact variants from the industry name and industry key
- returns the set of normalized terms used for later matching

Why it exists:

- industry names are often broader and noisier than company names
- this helper creates practical matching tokens without keeping every stopword-heavy phrase

`_filter_industry_candidate_links(page_url, links, industry)`

- runs the shared `filter_article_links(...)` helper first
- skips noisy CNBC URLs
- keeps only links whose text or URL matches the industry variants

Why it exists:

- it plays the same role as the company-specific filter, but tuned for industry text matching

`_save_followed_article_links(source_page_url, candidate_links, industry, ...)`

- collects article URLs from the filtered candidate links
- fetches those article pages with `crawl_article_pages(...)`
- reuses already-saved articles when possible
- applies recency rules
- computes normalization fields and score bundle
- writes the final record through `add_industry_news_article(...)`

Why it exists:

- this is the final save step for the industry pipeline
- unlike the company pipeline, it still does its own smaller article fetch batch per source-page context

That difference is the main reason the industry pipeline feels older than the company one.

### [macroNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/macroNewsPipeline.py)

This is the macro/economic data pipeline.

It is separate from the company and industry article workflow.

Instead of running the search-page to article-page pattern, it parses a more structured macro source and writes macro event-like data.

### [job_builder.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/job_builder.py)

This file builds the structured job dictionaries used by the pipelines.

Important helpers:

- `build_company_source_job(...)`
- `build_company_source_jobs(...)`
- `build_yahoo_news_jobs(...)`
- `build_industry_source_jobs(...)`
- `group_jobs_by_url(...)`
- `unique_job_urls(...)`

This file is important because it centralizes job creation instead of having each pipeline build ad hoc URL dictionaries inline.

### [_shared.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_shared.py)

Shared import and helper surface for the pipelines.

It re-exports the common pieces they depend on from:

- `core/`
- `engine/`
- `processing/`
- `sources/`

The point is to keep pipeline imports stable and compact.

### [_internal.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_internal.py)

Small internal pipeline-only helpers.

Current responsibilities:

- normalize topic/company/industry text for matching
- blacklist noisy CNBC paths
- test whether link text or URL matches a set of topic variants

### [_constants.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/_constants.py)

Stores pipeline-specific constants such as:

- company name suffixes
- industry stopwords

### [__init__.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/__init__.py)

Package marker for the folder.

## How The Folder Fits Into The System

Typical company flow:

1. load companies
2. build source jobs
3. optionally add Yahoo-derived article jobs
4. crawl source pages
5. filter discovered links
6. build one article crawl batch
7. reuse or fetch article pages
8. score and save

Typical industry flow:

1. load industries
2. build source jobs
3. crawl source pages
4. filter discovered links
5. follow article pages
6. score and save

## Mental Model

`pipelines/` is the orchestration layer.

It is where the scraper's "business logic" lives:

- what entities are being scraped
- what sources they use
- what counts as a relevant article
- when a URL is worth following
- how the final article gets saved
