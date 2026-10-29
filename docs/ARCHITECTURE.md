# Architecture

This document is the current source of truth for where new code belongs.

```text
React dashboard ──> Flask routes ──> API support ──> SQLite helpers/schemas
                                  └──> runtime JSON / broker adapters

News ingestion ──> agent pipeline ──> strategist/manager ──> deterministic execution
```

## Runtime Boundaries

| Concern | Preferred location | Notes |
| --- | --- | --- |
| API route | `api_routes/<feature>.py` | Register it in `api.py`; keep request parsing here. |
| API use case | `api_support/<feature>.py` | Read/shape data here; no Flask objects. |
| Market persistence | `Data/market_db_modules/` | Import from the owning module, e.g. `market_db_modules.queries`; there is no facade. |
| News persistence | `Data/news_db_modules/` | Import from the owning module, e.g. `news_db_modules.article_store`; there is no facade. |
| Database selection | `Data/db_common.py` | Store functions take `db_path: Path \| str \| None = None`; `None` means the current database (`current_database()`). Production uses the `DB_PATH` env value. |
| Database in tests | `use_database(path)`, `tests/db_fixtures.temporary_database(self)` | Swap the current database; never patch `DB_PATH`. |
| Database setup | `db_common.forget_setup()` | Setup runs once per database file per process, keyed on the file's realpath and `(st_dev, st_ino)`. Restoring a file in place keeps its inode, and a deleted and recreated file may reuse it: stop services first or call `forget_setup()`. |
| Database scope | `use_database`, `DB_PATH` | `use_database` does not reach new threads or thread-pool workers. Subprocesses follow the `DB_PATH` env var. |
| Database schema | `Data/market_schema.sql`, `Data/news_schema.sql` | Use additive, idempotent `CREATE ... IF NOT EXISTS` migrations. The market setup stamps the database with a hash of its files (`Data/*.py`, `Data/*.sql`, `Data/market_db_modules/`, `config/risk_clusters.csv`) and reruns only when that hash changes, so a new migration must live in those files. |
| Agent input builders | `Python Scripts/agentCallers/agent_builders/` | Deterministic payload construction only. |
| Agent stage | `Python Scripts/agentCallers/agent_stages/` | Prompt, structured model response, and validation. |
| Agent data helper | `Python Scripts/agentCallers/agent_helpers/` | Database and market-context loading. |
| Trading/runtime action | `Python Scripts/services/` | Deterministic guards, execution, broker adapters, and status I/O. |
| Dashboard feature | `web_dashboard/src/` | Keep API fetch, view state, and feature styles together. |
| Tests | `tests/test_<feature>.py` | Prefer isolated SQLite DBs and mocked provider/broker calls. |

## Supported Entrypoints

Use `scripts/project.py` for local commands. The runtime entrypoints are:

- `api.py` — Flask API
- `Python Scripts/main.py` — worker/trading loop
- `Python Scripts/news_collector_main.py` — ingestion loop
- `Python Scripts/option_manager_main.py` — existing-position loop
- `Python Scripts/agentCallers/offline_manager_evaluation.py` — offline evaluation

Docker Compose remains the source of truth for the integrated production-like stack.

## Compatibility Policy

Import roots are declared once in `pyproject.toml` (see [DEVELOPMENT.md](DEVELOPMENT.md)); modules never edit `sys.path`. Put code in the package boundary above and import it from there. Do not add re-export wrappers: move callers to the canonical module instead (the database and `Trading.py` facades were removed this way).

## Configuration

Read settings through `services.settings.get_settings()` at call time; never read the
environment when a module is imported. See [DEVELOPMENT.md](DEVELOPMENT.md).

## Safety Boundaries

- LLMs explain and classify structured, grounded inputs.
- Python validates model references and calculates deterministic scores and trade gates.
- API/dashboard review features are read-only unless they explicitly call a runtime execution service.
- Demo Mode is a static fixture and must never write data or place an order.
