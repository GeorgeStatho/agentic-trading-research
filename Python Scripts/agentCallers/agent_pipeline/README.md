# agent_pipeline

This folder contains the orchestration layer that ties the individual stages together into the current end-to-end stock-selection pipeline.

## Files

### `main.py`

Purpose: compatibility-facing facade and direct-script entrypoint for the pipeline package.

Functions:
- `_configure_console_logging()`: configures basic console logging for direct script runs.
- re-exports the public pipeline functions from the smaller concern-specific modules below.

### `ranking.py`

Purpose: choose top sectors and industries from saved upstream ranking signals.

Functions:
- `get_current_rankings(...)`: returns the current ranked sector/industry view.
- `_get_top_sector_keys(...)`: chooses top sector keys from saved sector-interest scores.
- `_get_top_industry_keys(...)`: chooses top industry keys inside one sector from saved industry-interest scores.

### `target_selection.py`

Purpose: convert rankings into selected companies and compact company summary payloads.

Functions:
- `collect_ranked_companies_for_industry(...)`: loads the selected industry and chooses the top pipeline target companies for it.
- `build_company_opportunist_summary(result)`: converts a company opportunist result into a compact summary block for the pipeline output.
- `get_current_pipeline_targets(...)`: computes the current set of sectors, industries, companies, and company IDs that the pipeline would target.

### `cleanup.py`

Purpose: remove saved opportunist outputs for the currently selected pipeline targets.

Functions:
- `clear_current_pipeline_targets(...)`: deletes saved opportunist processing/impact rows for the currently targeted sectors, industries, and companies.

### `existing_db_view.py`

Purpose: build the pipeline result view using persisted DB state only.

Functions:
- `run_agent_pipeline_from_existing_data(...)`: returns the current pipeline view without scraping.

### `news_refresh.py`

Purpose: run the scrape plus downstream stage execution path.

Functions:
- `run_news_collection_pipeline(...)`: runs the current sector -> industry -> company opportunist pipeline and returns the output structure.
- `run_agent_pipeline(...)`: backward-compatible alias for the refresh path.

### `__init__.py`

Purpose: package marker for the `agent_pipeline` folder.
