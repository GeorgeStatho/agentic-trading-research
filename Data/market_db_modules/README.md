# Market DB Modules

This folder contains the internal implementation modules for [`market_db.py`](../market_db.py).

## Structure

- `shared.py`: shared constants, timestamp helpers, coercion, and lookup utilities
- `entities.py`: sector, industry, company, and ranking persistence
- `refresh_state.py`: market-data hydration refresh tracking
- `snapshots.py`: company price snapshot persistence
- `trade_executions.py`: option trade execution persistence
- `history_records.py`: manager decision history persistence and P/L update helpers
- `history_links.py`: matching manager decisions to trade executions
- `bootstrap.py`: schema initialization and legacy-schema migration
- `loaders.py`: sector definition and sector tree loaders
- `queries.py`: read helpers for companies, rankings, snapshots, executions, and decision history

## Import guidance

- External callers should keep importing from `market_db`.
- Modules in this folder are internal implementation details and use package-relative imports.
- If you add a new public helper, export it from [`market_db.py`](../market_db.py) to preserve the stable facade.
