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

Yahoo news, Alpaca News (Benzinga) and the Fool, Morningstar, and GuruFocus APIs are extra article discovery inputs for the company pipeline. None of them drives the full pipeline by itself.

## Folder Layout

The most important current files are:

- [pipelines/companyNewsPipeline.py](pipelines/companyNewsPipeline.py)
- [pipelines/industryNewsPipeline.py](pipelines/industryNewsPipeline.py)
- [pipelines/sectorNewsPipeline.py](pipelines/sectorNewsPipeline.py)
- [pipelines/USNewsPipeline.py](pipelines/USNewsPipeline.py)
- [pipelines/worldNewsPipeline.py](pipelines/worldNewsPipeline.py)
- [pipelines/macroNewsPipeline.py](pipelines/macroNewsPipeline.py)
- [pipelines/job_builder.py](pipelines/job_builder.py)
- [pipelines/_orchestration.py](pipelines/_orchestration.py)
- [pipelines/_article_follow.py](pipelines/_article_follow.py)
- [pipelines/_sector_rss.py](pipelines/_sector_rss.py)
- [pipelines/_regionNewsPipeline.py](pipelines/_regionNewsPipeline.py)
- [pipelines/_internal.py](pipelines/_internal.py)
- [pipelines/_constants.py](pipelines/_constants.py)
- [engine/article_scraper.py](engine/article_scraper.py)
- [engine/article_extraction.py](engine/article_extraction.py)
- [market_data/yFinanceNews.py](market_data/yFinanceNews.py)
- [APIs/alpacaNewsAPI.py](APIs/alpacaNewsAPI.py)

Import the canonical modules under `pipelines/`, `engine/`, `processing/`,
`extractors/`, `sources/`, `market_data/` and `APIs/`. The old root-level
compatibility wrappers were removed. The full preferred import map lives in
[INTERFACE_GUIDE.md](../../docs/INTERFACE_GUIDE.md).

## Core Mental Model

The easiest way to understand the news pipelines is to separate them into three layers:

1. planning
2. crawling
3. extraction and persistence

Planning decides what URLs should be visited.

Crawling fetches those URLs and returns structured page results. Pipelines do this only through [engine/page_fetch.py](engine/page_fetch.py).

Extraction and persistence decide which results are worth keeping, normalize the article content, score it, and write it to the database.

## Job Shape

The planning layer now builds explicit job dictionaries instead of having each pipeline assemble URLs ad hoc.

Job building lives in [pipelines/job_builder.py](pipelines/job_builder.py).

Important helpers:

- `build_company_source_jobs(...)`
- `build_industry_source_jobs(...)`
- `build_company_source_job(...)`
- `build_yahoo_news_jobs(...)`
- `build_sector_rss_jobs(...)`
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

The sector, U.S., and world pipelines now use the `article` path heavily because they start from CNBC RSS feeds rather than from search pages.

## Shared Helpers

[pipelines/_internal.py](pipelines/_internal.py) contains small pipeline-only helpers such as:

- company and industry match normalization
- CNBC blacklist checks
- "does this link text or URL actually mention the target thing?" logic

[pipelines/_constants.py](pipelines/_constants.py) holds shared constants such as industry stopwords and Fool exchange slugs.

## Company Pipeline Flow

The main entrypoints are:

- `get_company_news(company_identifier)`
- `get_all_company_news()`

Both live in [pipelines/companyNewsPipeline.py](pipelines/companyNewsPipeline.py).

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

`_build_company_jobs(company)` combines three sources of jobs:

1. normal configured company source jobs from `build_company_source_jobs([company])`
2. article jobs from the stock-data APIs in `API_SOURCES` (`_build_api_jobs(...)`): Fool, Morningstar, GuruFocus and Alpaca News
3. Yahoo-discovered article jobs from `yFinanceNews.py`

The API portion calls each API's `get_stock_data(symbol, limit=10)`, collects the `(title, url)` pairs from its `articles`, `analysis`, and `transcripts` lists, and turns them into `article` jobs. A failing API is logged and skipped.

Alpaca News (`APIs/alpacaNewsAPI.py`) serves Benzinga news through the same Alpaca account used for trading (`PUBLIC_KEY`/`PRIVATE_KEY`; the free plan includes it). It differs from the other APIs in one way: the article body arrives with the listing. `get_stock_data` makes one `NewsRequest(symbols=symbol, limit=10, include_content=True)` per company and keeps each article's body in an in-memory cache (`CONTENT_CACHE_LIMIT`, most recent first). When the article stage fetches the `benzinga.com` URL, `get_article_content` answers from that cache, so benzinga.com is never scraped. A URL that is not cached raises `ArticleContentUnavailable`: the fetch layer leaves it out of the result (missing, retried next run) instead of recording a failure. If the Alpaca keys are missing, the source returns no articles and logs one warning.

The Yahoo portion works like this:

1. `get_company_news_items(symbol)` asks `yf.Search(symbol).news`. `Ticker.get_news` returns nothing for many tickers on current yfinance, so it is only the fallback. Search is a free-text query, so only items whose `relatedTickers` include the symbol are kept (`BRK.B` matches `BRK-B`); if none survive, it falls back to `Ticker.get_news`
2. `extract_title_url_publisher(...)` reduces those items to `(title, url, publisher)` triples; it understands both the flat Search shape and the nested `content` shape
3. only URLs from already-allowed scraper sources survive that filter (`finance.yahoo.com` is allowed, with `/news/` and `/articles/` URL patterns)
4. `build_yahoo_news_jobs(...)` turns those triples into normal `article` jobs; the publisher (e.g. "Business Wire", "Reuters") is stored in the saved article's `raw_json` as `provenance.publisher`, so `news_db_modules/provenance.py` credits the original wire as the article's root source

That means the APIs and Yahoo act as extra article discovery sources, not as special storage paths.

### Step 3: Split jobs by type

`_process_company_jobs(...)` hands the full job list to `run_mixed_job_orchestration(...)`, which splits it into:

- `search` jobs (plus `listing` jobs for industries)
- `article` jobs

This is one of the most important design ideas in the current company pipeline.

Some inputs are discovery pages.

Some inputs are already article URLs.

The pipeline keeps those separate until it is time to batch the article fetch. The split and the rest of the flow are handled by `run_mixed_job_orchestration(...)` in [pipelines/_orchestration.py](pipelines/_orchestration.py), which the industry pipeline uses too.

### Step 4: Crawl all search pages together

If there are search jobs, the pipeline groups them by URL and fetches all source pages in one call:

- `fetch_source_pages(urls)`

This is defined in [engine/page_fetch.py](engine/page_fetch.py), which hands the URLs to `crawl_articles(urls)` in [engine/article_scraper.py](engine/article_scraper.py). A crashed crawl returns no pages and logs a warning instead of raising.

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

### Step 5: Collect candidate links

`_company_candidate_links(...)` returns the links of a search page whose source supports `search`, unfiltered. The links already come from company-specific search pages, so the link text is not checked for a company-name match. Whether each link is worth following is decided in step 6, by one verdict per link.

### Step 6: Build one shared article batch

After the search pages are crawled, `build_search_article_save_requests(...)` turns the candidate links into `ArticleSaveRequest` objects. Direct article jobs (Yahoo and the APIs) become requests of the same shape through `direct_article_save_request(...)`.

Then `run_article_save_requests(...)` gives every link one verdict with `judge_links(...)` in [pipelines/_link_policy.py](pipelines/_link_policy.py), before anything is fetched. A verdict is `fetch`, `reuse` or `skip`, and a skip carries a reason: `no_href`, `duplicate`, `excluded`, `disallowed_source`, `not_article_url`, `blacklisted`, `stale_url_date`, `stale_existing`, `irrelevant_existing`, `failed_url` or `limit`. The whole run uses one batched database lookup for saved articles and failed URLs.

- An article that is already saved and recent is `reuse`: it only gets a link row. Reuse is free: it does not count toward the 10-new-articles-per-page limit, and saved counts include it. On a company search page, a reused article must also name the company (name or ticker, as whole words, in the link text or URL); this guards against "trending" links on CNBC quote pages. Direct jobs and industry links reuse without that check.
- A URL that failed 3 times, or permanently (HTTP 403 or 404), is skipped on every path.
- Recency uses the per-source maximum age (`max_article_age_days` in `config/source.json`, default 14 days): the date in a CNBC URL, the stored date of a saved article, and, after fetching, the extracted date. The RSS 3-day window is only a discovery window for choosing feed items.

Then it fetches the `fetch` verdicts once, deduplicated by normalized URL, with `fetch_articles(article_urls)` from [engine/page_fetch.py](engine/page_fetch.py). Fool, Investing, Morningstar, and GuruFocus URLs go through their `APIs/` article-content functions; everything else, and any API failure, goes through `crawl_article_pages(...)`.

This keeps API-discovered and Yahoo-discovered article URLs in the same fetch batch as the URLs discovered from normal search pages.

### Step 7: Save followed articles

Each save request's verdicts are processed by `save_link_verdicts(...)` in [pipelines/_article_follow.py](pipelines/_article_follow.py).

That function is the main "final mile" of every news pipeline. It only walks `fetch` and `reuse` verdicts:

1. a `reuse` verdict only adds a link row for this company and leaves the article row untouched
2. a `fetch` verdict uses the pre-fetched article result, checks the extracted date, computes article scores, and inserts the article and its company link in one transaction
3. a URL that appears in several requests of the same run is inserted once; later requests only add their link row (the shared `outcomes` map)

The entity-specific part is a `NewsEntity` (kind, id, label, identity fields, and a link function such as `link_company_to_article`) handed in by the pipeline. It works from the batch that `run_article_save_requests(...)` fetched.

## Industry Pipeline Flow

The main entrypoint is:

- `get_all_industry_news()`

This lives in [pipelines/industryNewsPipeline.py](pipelines/industryNewsPipeline.py).

The industry pipeline runs the same shared orchestration as the company pipeline: `run_mixed_job_orchestration(...)`, then one combined article fetch and `save_link_verdicts(...)`.

### What is the same

It:

- builds structured jobs
- crawls source pages
- extracts candidate links
- keeps only the candidate links that match the industry
- fetches all article pages in one batch
- scores and saves articles through the shared code

### What is different

The industry pipeline does not have the Yahoo or API article discovery that the company pipeline has. Its jobs are only configured `listing` and `search` pages.

It also defines its own relevance rules (see below) and passes `by_source_type=True`, so it reports saved counts separately for `listing` and `search` pages.

### Industry matching logic

The industry pipeline uses `_build_industry_match_variants(...)` and `_filter_industry_candidate_links(...)`.

It looks for:

- industry name
- industry key
- simplified token combinations

Listing pages are narrower: `_listing_topic_links(...)` keeps links whose text or URL contains the industry name. Source, article-pattern, blacklist, recency and failed-URL checks all happen later in `judge_links(...)`.

## Macro Pipeline Flow

[pipelines/macroNewsPipeline.py](pipelines/macroNewsPipeline.py) is a separate workflow.

It is not the same "search page -> candidate links -> article follow" pattern.

Instead, it parses a structured macro/economic source and stores event-like records rather than normal article records.

So if you are trying to understand the article scraping architecture, the company and industry pipelines are the right starting point.

## Sector, U.S., and World Pipelines

These three start from CNBC RSS feeds instead of search pages, so they skip the source-page crawl entirely.

- [pipelines/sectorNewsPipeline.py](pipelines/sectorNewsPipeline.py) reads the feeds mapped to a sector in `Data/sector_rss_feeds.json`.
- [pipelines/USNewsPipeline.py](pipelines/USNewsPipeline.py) and [pipelines/worldNewsPipeline.py](pipelines/worldNewsPipeline.py) each read one fixed feed through `make_region_pipeline(...)` in [pipelines/_regionNewsPipeline.py](pipelines/_regionNewsPipeline.py).

Each feed is read with `fetch_feed(url)` from [engine/page_fetch.py](engine/page_fetch.py). A feed that cannot be fetched or parsed yields no items, so one bad feed does not stop the others.

For each recent RSS item they build a `direct_article_save_request(...)`, with the item's RSS description as the summary, and call `run_article_save_requests(...)`. After that it is the same fetch and `save_link_verdicts(...)` path described above, with a `NewsEntity` for the sector, U.S. news, or world news.

## Scraping Engine

[engine/page_fetch.py](engine/page_fetch.py) is the one fetch entry point for the pipelines. It provides `fetch_source_pages(urls)`, `fetch_articles(urls)` and `fetch_feed(url)`, picks the site API or the crawler by hostname, and contains backend failures. See [engine/README.md](engine/README.md) for the details, including the "missing, not failed" rule below.

[engine/article_scraper.py](engine/article_scraper.py) is the crawling engine behind it. It chooses Scrapy or Playwright internally, and provides two public helpers:

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

[engine/article_extraction.py](engine/article_extraction.py) is responsible for turning an HTML response into:

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

Those extractor modules live under [extractors/](extractors).

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

- [sources/source_config.py](sources/source_config.py)
- [config/source.json](../../config/source.json)

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
- `sector_news_articles`
- `us_news_articles`
- `world_news_articles`
- `failed_urls`

`news_articles` holds each article once. The other five are link tables that connect an article to a company, industry, sector, the U.S. feed, or the world feed.

Every pipeline writes through `save_link_verdicts(...)`:

- a new article is inserted with `add_news_article(...)` and linked to its entity in one transaction
- an already-saved article only gets a link row, so `news_articles` is untouched
- the link row comes from the entity's link function in `news_db_modules/links.py`: `link_company_to_article`, `link_industry_to_article`, `link_sector_to_article`, `link_us_article`, or `link_world_article`

A new article's `summary` is the link text: the anchor text from a search or listing page, the RSS description for sector, U.S., and world articles, or `None`. The entity name is never stored as a summary. Older rows that still hold one (for example `World News`) can be cleared with [../../scripts/clear_placeholder_summaries.py](../../scripts/clear_placeholder_summaries.py), which is a dry run unless you pass `--apply`.

Fetched pages whose extraction failed are written to `failed_urls`. A URL that has failed 3 times, or permanently (HTTP 403 or 404), gets a `failed_url` skip verdict on every path, so later runs do not hit known-bad URLs again. A URL that is missing from the fetch result because a backend crashed is not recorded there; it is only logged and tried again next run. An outage therefore shows up as "saved 0" plus warnings in the logs.

## Testing Offline

Wrap a pipeline call in `with use_fetcher(FixtureFetcher(pages=..., articles=..., feeds=...)):` (both from `engine.page_fetch`) to run it without the network. See [pipelines/README.md](pipelines/README.md#testing-pipelines-offline).

## Logging

Logs are written under:

- `Stock-trading-experiment/Data/logs/`

The main logging helper is:

- [core/scrape_logging.py](core/scrape_logging.py)

In practice, the logs show:

- which source-page crawl started
- which article-page crawl started
- which links were filtered out
- which URLs failed
- which articles were reused from the database
- which articles were saved

If you are debugging runtime behavior, the logs are usually the fastest way to confirm which phase of the pipeline you are in.

## Yahoo News Helper

[market_data/yFinanceNews.py](market_data/yFinanceNews.py) is intentionally small.

Its job is:

1. ask `yfinance` for company news items
2. reduce the raw Yahoo payload to `(title, url)` pairs
3. keep only URLs from sources your scraper already supports

So the Yahoo helper is not doing extraction, scoring, or saving by itself.

It is only feeding extra article URLs into the company pipeline.

## Yahoo Market Data Side

The market-data system lives nearby but is separate from the article pipeline.

Important files include:

- [market_data/yFinanceData.py](market_data/yFinanceData.py)
- [market_data/yfinance_client.py](market_data/yfinance_client.py)
- [market_data/yFinanceRealTime.py](market_data/yFinanceRealTime.py)

That code is responsible for:

- sector and industry ingestion
- company ingestion
- ranking snapshots
- price snapshots
- throttling and retrying Yahoo requests

This is a separate database path from the article/news scraper.

## Current Differences To Keep In Mind

All the news pipelines now run the same shared code: `run_article_save_requests(...)` fetches the combined article batch and `save_link_verdicts(...)` saves it. What still differs is how each pipeline discovers articles:

- the company pipeline uses configured search pages plus API and Yahoo article jobs
- the industry pipeline uses configured listing and search pages
- the sector, U.S., and world pipelines use CNBC RSS feeds only
- Yahoo news and the Fool, Morningstar, and GuruFocus APIs are discovery inputs for the company pipeline, not standalone scraping pipelines

## Quick Trace Example: `get_company_news(...)`

If you want one concrete path to keep in your head, this is the current company flow:

1. resolve the company from the database
2. build search jobs from configured sources
3. fetch Fool, Morningstar, GuruFocus, and Yahoo news items and convert allowed title/URL pairs into direct article jobs
4. fetch all search pages together with `fetch_source_pages(...)`
5. extract links from those pages
6. keep every link of a supported search page as a candidate (company relevance only gates reuse of already-saved articles)
7. combine those candidate links with the direct article jobs
8. judge every link (fetch, reuse or skip), then fetch the `fetch` URLs together in one batch with `fetch_articles(...)` (API extractors first, `crawl_article_pages` otherwise)
9. link already-saved articles to the company; score and insert the new ones
10. return the saved count for the company

## Suggested Reading Order

If you want to understand the system with the least confusion, read the files in this order:

1. [pipelines/companyNewsPipeline.py](pipelines/companyNewsPipeline.py)
2. [pipelines/job_builder.py](pipelines/job_builder.py)
3. [pipelines/_orchestration.py](pipelines/_orchestration.py)
4. [pipelines/_article_follow.py](pipelines/_article_follow.py)
5. [pipelines/_internal.py](pipelines/_internal.py)
6. [engine/article_scraper.py](engine/article_scraper.py)
7. [engine/article_extraction.py](engine/article_extraction.py)
8. [pipelines/industryNewsPipeline.py](pipelines/industryNewsPipeline.py)

That order gives you the company pipeline first, then the shared machinery, then the industry variant. The RSS-based sector, U.S., and world pipelines are covered in their own section above.
