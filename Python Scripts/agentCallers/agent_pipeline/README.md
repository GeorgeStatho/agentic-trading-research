# agent_pipeline

This folder contains the orchestration layer that ties the individual stages together into the current end-to-end stock-selection pipeline.

## Files

### `main.py`

Purpose: choose sectors, industries, and companies from saved upstream signals; run the downstream opportunist stages; and build the final pipeline result JSON.

Functions:
- `_configure_console_logging()`: configures basic console logging for direct script runs.
- `_slice_companies(companies, limit)`: truncates the company list to the requested count.
- `_dedupe_preserving_order(values)`: removes duplicates while keeping the original order.
- `collect_ranked_companies_for_industry(industry_identifier, ...)`: loads the selected industry and chooses the top pipeline target companies for it.
- `build_company_opportunist_summary(result)`: converts a company opportunist result into a compact summary block for the pipeline output.
- `_get_top_sector_keys(...)`: chooses top sector keys from saved sector-interest scores.
- `_get_top_industry_keys(sector_key, ...)`: chooses top industry keys inside one sector from saved industry-interest scores.
- `get_current_pipeline_targets(...)`: computes the current set of sectors, industries, companies, and company IDs that the pipeline would target.
- `_count_rows_for_ids(...)`: counts rows in a table for a supplied ID list.
- `_load_article_ids_for_target_rows(...)`: loads distinct article IDs associated with saved target-stage impacts.
- `clear_current_pipeline_targets(...)`: deletes saved opportunist processing/impact rows for the currently targeted sectors, industries, and companies.
- `run_agent_pipeline(...)`: runs the current sector -> industry -> company opportunist pipeline and returns the output structure.

### `__init__.py`

Purpose: package marker for the `agent_pipeline` folder.
