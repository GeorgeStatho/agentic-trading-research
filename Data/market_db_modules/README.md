# Market DB Modules

This folder contains the market persistence modules. Callers import from them directly; there is no facade.

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
- `compact_storage.py`: content-addressed `json_blobs` storage. Repeated JSON subtrees in `candidate_snapshots` are stored once, zlib-compressed, and unpacking rebuilds the exact original text, so hash checks still pass. `scripts/compact_db.py` converts rows written before it
- `account_positions.py` (+ `account_positions_schema.sql`): follower-account positions, plus `account_summary` / `follower_report` for `scripts/account_report.py` and `/api/follower-accounts`
- `evidence_refresh.py`: the company-evidence request queue. `due_refresh_companies` picks the companies the collector handles each cycle (`COMPANY_EVIDENCE_COMPANIES_PER_CYCLE`), in this order: parent research-session requests, then companies the strategist/manager is about to see (active `pipeline_selection` interest with scope `manager`, `MANAGER_SCOPE`), then other pipeline selections, then the oldest request

## Import guidance

- Import from the owning module, e.g. `market_db_modules.queries`.
- Modules in this folder use package-relative imports.
- Store functions take `db_path=None`, meaning the current database (`db_common.current_database()`). Do not add a re-export module.
