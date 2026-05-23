# Engine Folder Guide

This folder contains the public crawl runtime.

## Responsibilities

- run Scrapy crawls
- extract generic links from source pages
- normalize article HTML into a shared `ArticleExtractionResult`
- optionally use Playwright for pages explicitly marked as dynamic

## Public Runtime Model

- `article_scraper.py`: crawl runner and shared spider
- `article_extraction.py`: generic article parsing
- `playwright_runner.py`: generic rendered-page support
- `listing_page_helper.py`: listing-page filtering helpers

The public branch intentionally avoids source-specific selectors and domain rules. Real production extraction behavior lives in the private repository.
