# News DB Modules

This folder contains the internal implementation modules for [`news_db.py`](../news_db.py).

## Structure

- `article_store.py`: canonical article insert/update and dedupe logic
- `bootstrap.py`: schema initialization and one-off migrations
- `entity_articles.py`: company, industry, and sector article attachment helpers
- `global_articles.py`: U.S. and world article attachment helpers
- `impacts.py`: opportunist and strategist result persistence
- `links.py`: link-table upserts between articles and scopes
- `loaders.py`: bulk article loaders
- `macro.py`: macro-event inserts and bulk loading
- `processing.py`: processed-article markers for downstream stages
- `queries.py`: read helpers and failed-URL persistence
- `shared.py`: shared constants, normalization imports, and small coercion helpers

## Import guidance

- External callers should keep importing from `news_db`.
- Modules in this folder are internal implementation details and use package-relative imports.
- If you add a new public helper, export it from [`news_db.py`](../news_db.py) to preserve the stable facade.
