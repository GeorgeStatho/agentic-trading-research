# News DB Modules

This folder contains the news persistence modules. Callers import from them directly; there is no facade.

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

- Import from the owning module, e.g. `news_db_modules.queries`.
- Modules in this folder use package-relative imports.
- Store functions take `db_path=None`, meaning the current database (`db_common.current_database()`). Do not add a re-export module.
