# Webscraping Pipeline Guide

This public repository keeps the scraper architecture visible while removing the real source mix and extraction logic.

## Public Pipeline Shape

The public flow is still the same:

1. source planning builds crawl jobs
2. source pages are fetched with Scrapy
3. candidate article links are extracted
4. article HTML is normalized into a common shape
5. normalized articles can be mapped to companies and passed into LLM analysis
6. downstream agent stages decide whether a trade candidate exists

In shorthand:

`news scraping -> article normalization -> company extraction -> LLM analysis -> trade candidate generation`

## What Is Public

- scraper architecture
- pipeline interfaces and helper modules
- generic Scrapy + Playwright setup
- demo-safe source configuration
- example spider against a harmless demo site
- sample fake article JSON
- mocked-HTML tests for extraction

## What Is Private

- real news source list
- exact URLs and endpoints
- selectors and page-specific parsing logic
- login and account handling
- throttling and block-avoidance behavior
- source ranking weights
- filtering and deduplication heuristics tuned on production data
- raw scraped data

## Important Folders

- `pipelines/`: planning and orchestration
- `engine/`: generic crawl and extraction runtime
- `sources/`: demo-safe source configuration
- `extractors/`: public extractor interfaces routed to generic demo behavior
- `examples/`: public demo spider and sample output

## Public Demo Artifacts

- `examples/demo_spider.py`: example Scrapy spider using `quotes.toscrape.com`
- `examples/sample_fake_article.json`: sample normalized article payload
- `tests/test_webscraping_public_demo.py`: mocked-HTML coverage for the public scraper surface

## Notes

The real production scraper is intentionally not reproducible from this public repo alone. The goal here is to document the system design and provide a safe demo path without disclosing the practical source mix or extraction edge.
