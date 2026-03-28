# Webscraping Workflow

This folder contains the deterministic data-gathering layer for the project.
It is responsible for:

- building source URLs for industry and company news
- crawling source pages with Scrapy
- filtering candidate article links before deep fetches
- extracting article title, body, and publish time
- scoring saved articles with deterministic evidence signals
- storing articles and article-to-entity links in SQLite
- loading Yahoo sector, industry, and company market data

## High-Level Flow

The article/news scraping flow works in two stages:

1. crawl source pages
2. follow only the filtered article links

This is intentional. Source pages such as search pages or listing pages are used
for shallow discovery first, and only a smaller set of candidate article URLs
are fetched deeply afterward.

## Main News Pipelines

The main entrypoints are:

- [pipelines/industryNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/industryNewsPipeline.py)
- [pipelines/companyNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/companyNewsPipeline.py)
- [pipelines/macroNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/macroNewsPipeline.py)

There are also top-level compatibility wrappers with the old file names:

- [industryNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/industryNewsPipeline.py)
- [companyNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/companyNewsPipeline.py)
- [macroNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/macroNewsPipeline.py)

## Industry And Company News Flow

### 1. Build source jobs

Each pipeline starts by reading the relevant entities from the database:

- industries via `get_all_industries()`
- companies via `get_all_companies()`

Then it builds source jobs:

- chooses the configured sources from [urlFactories.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/urlFactories.py)
- fills `{query}` placeholders with the entity search term
- stores a `jobs_by_url` mapping so each crawled page can later be mapped back to its entity and source type

This stage is handled by:

- `build_source_jobs(...)` in the industry and company pipelines
- `build_source_url(...)` in [core/CommonPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/CommonPipeline.py)

### 2. Crawl source pages

All source URLs are crawled in one Scrapy run through:

- [article_scraper.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/article_scraper.py)

Important behavior:

- source pages are fetched first
- the source page is normalized with `extract_from_response(...)`
- discovered links are collected from the page
- for known domains, a site-specific search extractor is used automatically

Current site-specific search extractors:

- [marketwatch_extractor.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/marketwatch_extractor.py)
- [cnbc_extractor.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/cnbc_extractor.py)
- [investing_extractor.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/investing_extractor.py)

If a source page does not match a dedicated extractor, the scraper falls back to generic link inventory collection.

### 3. Filter candidate links

After the source pages are crawled, the pipelines decide which discovered links
are worth following.

Shared filtering happens in:

- [core/CommonPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/CommonPipeline.py)

The shared filter checks:

- source allowlist from `config/source.json`
- source-specific URL patterns from `source.json`
- previously failed URLs
- already-saved article URLs in the database

Then each pipeline adds its own relevance checks:

- company pipeline:
  - requires company-name or symbol variants to match the link text or URL
  - blacklists noisy CNBC paths
- industry pipeline:
  - requires industry-name variants to match the link text or URL
  - blacklists noisy CNBC paths

This happens before article-page follow requests are made.

### 4. Follow candidate article URLs

Only links that survive filtering are followed with:

- `extract_article(...)` in [article_extraction.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/article_extraction.py)

Before fetching a candidate article URL, the pipelines first check whether the
article already exists in the database:

- `fetch_existing_article_by_url(...)` in [core/CommonPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/CommonPipeline.py)

If the article is already stored:

- the article body/title/publish time are reused
- no new network request is made for that article page
- the article can still be linked to a new company or industry

If the article is not already stored:

- the article page is fetched with `requests`
- the response is converted into a Scrapy `HtmlResponse`
- `extract_from_response(...)` is used to normalize the page

### 5. Extract article content

Article extraction tries site-specific logic first, then falls back to a generic parser.

Current site-specific article extractors:

- [marketwatch_extractor.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/marketwatch_extractor.py)
- [cnbc_extractor.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/cnbc_extractor.py)
- [investing_extractor.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/investing_extractor.py)

The generic fallback in [article_extraction.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/article_extraction.py) looks for:

- common metadata publish-time tags
- article/body/main paragraph containers

The extractor returns a normalized result with:

- `url`
- `title`
- `text`
- `published_at`
- `success`
- `error`

### 6. Recency checks and scoring

Before saving:

- stale articles are skipped
- recent articles are scored with deterministic rules

Shared scoring is computed in:

- [scoring.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/scoring.py)
- [core/CommonPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/CommonPipeline.py)

Current stored scores include:

- `age_days`
- `recency_score`
- `source_reputation_score`
- `directness_score`
- `confirmation_score`
- `independent_source_count`
- `factuality_score`
- `evidence_score`

These are deterministic scores, not model-generated confidence values.

### 7. Save to the database

Saved articles go into the shared `news_articles` table, and then are linked to
the relevant entity through join tables.

Industry path:

- `add_industry_news_article(...)`
- links article to `industry_news_articles`

Company path:

- `add_company_news_article(...)`
- links article to `company_news_articles`

Failed fetches are also tracked:

- `failed_urls` table

This allows future runs to skip URLs that repeatedly fail or appear permanently blocked.

## Macro News Flow

Macro data is handled separately in:

- [pipelines/macroNewsPipeline.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/pipelines/macroNewsPipeline.py)

That pipeline:

- fetches the Investing.com economic calendar
- parses macro event rows
- normalizes event data
- stores events through the news database helpers

This is more structured than the company/industry article flow because it is parsing event tables, not article search results.

## Source Configuration

Allowed domains and source metadata are controlled by:

- [source_config.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/source_config.py)
- [config/source.json](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/config/source.json)

This configuration controls:

- whether a source is allowed
- reputation score
- source type
- article URL patterns
- whether listing/search scraping is supported
- max article age rules

## Logging

Scrape activity is logged through:

- [core/scrape_logging.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/core/scrape_logging.py)

Current logs include:

- source-page crawl logs
- article-fetch and article-extraction logs
- pipeline filter and save decisions

Log files are written under:

- `Stock-trading-experiment/Data/logs/`

## Yahoo Market Data Flow

Yahoo sector/industry/company data is separate from article scraping, but it lives in this folder too.

Main files:

- [market_data/yFinanceData.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yFinanceData.py)
- [market_data/yfinance_client.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/market_data/yfinance_client.py)

Responsibilities:

- `yfinance_client.py`
  - talks to Yahoo
  - throttles requests
  - retries failed Yahoo calls
  - logs Yahoo request activity
- `yFinanceData.py`
  - cleans Yahoo payloads
  - shapes sectors, industries, and companies
  - writes JSON snapshots
  - stores market data in the market database

This path is not used for article scraping, but it is part of the broader data-gathering layer.

## Useful Mental Model

The easiest way to think about the article workflow is:

1. generate source/search/listing URLs
2. crawl those source pages once
3. collect links from those pages
4. filter the links aggressively
5. fetch only the surviving article pages
6. score and save the articles
7. link each saved article back to the company or industry

That separation between source-page discovery and article-page follow is the main reason the scraper is structured the way it is.
