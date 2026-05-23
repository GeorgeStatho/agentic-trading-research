# Extractors Folder Guide

This public repository keeps only demo-safe extractor interfaces.

The purpose of this folder is to show the shape of the scraping system:

- detect whether a response looks like a search/listing page
- extract candidate article links from that page
- extract article title, body, and publish time from a fetched article

## Public Layout

- `demo.py`: generic extractor logic used by the public examples
- compatibility modules such as `cnbc.py` or `marketwatch.py`: thin aliases that preserve the old import surface without exposing real selectors

## Design Pattern

Each extractor module still follows the same callable shape:

- `is_<site>_url(...)`
- `is_<site>_article_url(...)`
- `response_looks_like_<site>_search(...)`
- `extract_<site>_search_links(response)`
- `extract_<site>_article(response)`

In the public repo, those functions route to generic demo behavior. The private repo contains the real source-specific parsing logic.
