# Webscraping Architecture

Overview only; each folder's README holds the detail.

```text
pipelines -> engine/page_fetch -> engine/article_scraper (-> playwright_runner) | APIs
                                        |
                                   extractors (per-site parsing)
```

## Modules

- `pipelines/` holds the entry points (`*NewsPipeline.py`). `_orchestration.py`
  batches crawls per job; `_article_follow.py` follows article links, scores
  them, and persists them for a `NewsEntity`.
- `engine/page_fetch.py` is the single fetch entry point. Pipelines never call
  Scrapy, Playwright, or an API directly; a swappable fetcher (live or fixture)
  sits behind it. It dispatches to `engine/article_scraper.py` (Scrapy, with
  `engine/playwright_runner.py` for rendered pages) or `APIs/*`.
- `extractors/` parses individual sites. `engine/article_extraction.py` calls the
  extractor modules directly, for example `extractors.cnbc`.
- `sources/` is source policy: `source_config.py` reads `config/source.json`,
  and `urlFactories.py` builds search and feed URLs.
- `processing/` scores and normalizes articles and URLs.
- `core/` has the shared pipeline helpers (`CommonPipeline.py`) and scrape logging.
- `market_data/` fetches and hydrates market data (yfinance).
- `company_evidence/` is a separate SEC and release evidence stack that does not
  go through the news pipelines.

## Conventions

- Source-specific behavior belongs in source policy or an extractor, not a
  pipeline.
- Persist normalized URLs and link an existing article to each relevant entity.
- Import from the owning module; there are no re-export modules.
