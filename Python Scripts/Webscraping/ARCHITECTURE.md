# Webscraping Architecture

Overview only; each folder's README holds the detail.

```text
pipelines -> engine/page_fetch -> engine/article_scraper (-> playwright_runner) | APIs
                                        |
                                   extractors (per-site parsing)
```

## Modules

- `pipelines/` holds the entry points (`*NewsPipeline.py`). `_orchestration.py`
  runs one source-page crawl over the unique URLs of all jobs, then one batched
  article fetch; `_link_policy.py` gives every candidate link one verdict
  (fetch, reuse or skip) before anything is fetched, using one batched DB
  lookup per run; `_article_follow.py` scores and persists the verdicts for a
  `NewsEntity`.
- `engine/page_fetch.py` is the single fetch entry point: pipelines never crawl
  or open source pages, article pages, or RSS feeds themselves. A swappable
  fetcher (live or fixture) sits behind it and dispatches to
  `engine/article_scraper.py` (Scrapy, with `engine/playwright_runner.py` for
  rendered pages) or `APIs/*`. A failing backend is logged and its URLs are
  left out of the result (missing, not recorded as failed); nothing raises into
  a pipeline. The exceptions are the company pipeline's discovery calls (site
  API `get_stock_data`, yfinance) and the separate macro data pipeline
  (`requests`).
- `extractors/` parses individual sites. `engine/article_extraction.py` calls the
  extractor modules directly, for example `extractors.cnbc`.
- `sources/` is source policy: `source_config.py` reads `config/source.json` at
  the repo root, and `urlFactories.py` holds the source URL-template dicts.
  `core/CommonPipeline.py` (`build_source_url`) builds the URLs; RSS feed URLs
  live in `pipelines/_sector_rss.py` (from `Data/sector_rss_feeds.json`) and
  the US and world pipelines.
- `processing/` scores and normalizes articles and URLs.
- `core/` has the shared pipeline helpers (`CommonPipeline.py`) and scrape logging.
- `market_data/` fetches and hydrates market data (yfinance).
- `company_evidence/` is a separate SEC and release evidence stack that does not
  go through the news pipelines.

## Conventions

- Source-specific behavior belongs in source policy or an extractor, not a
  pipeline.
- Persist normalized URLs and link an existing article to each relevant entity.
- Link policy lives in `pipelines/_link_policy.py`: recency uses the per-source
  `max_article_age_days` (URL date, stored date, extracted date); the RSS
  3-day window is only a feed discovery window; failed URLs are skipped on
  every path.
- Import from the owning module; there are no re-export modules.
