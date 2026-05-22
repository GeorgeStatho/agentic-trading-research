# agent_builders

This folder contains builder modules that assemble structured inputs for later pipeline stages. These modules mostly resolve identifiers, load supporting data, and shape the payloads that the stage modules pass to models.

## Files

### `company_opportunity.py`

Purpose: resolve industries and companies, then build grouped company-selection payloads and linked company-article payloads.

Functions:
- `_find_industry(industry_identifier)`: resolves an industry by key or display name.
- `_find_company(company_identifier)`: resolves a company by symbol or display name.
- `_serialize_company_row(row)`: normalizes a company row into the simpler payload shape used by the pipeline.
- `_serialize_ranked_company_row(row)`: normalizes ranked-company rows that include ranking metadata.
- `get_industry_company_groups(industry_identifier)`: returns the selected industry plus its top companies, top growth companies, and top performing companies.
- `get_company_linked_articles(company_identifier)`: returns the company record plus all company-linked articles currently saved in the database.

### `opportunist_payload.py`

Purpose: build the combined sector opportunist input payload from sector RSS articles and high-confidence macro-to-sector signals.

Functions:
- `_build_payload_filters(...)`: serializes the current filtering settings into a payload-friendly structure.
- `_build_payload_sector(sector)`: serializes the sector identity block used in opportunist prompts.
- `build_opportunist_input(sector_identifier, ...)`: builds the full structured opportunist input for a sector.
- `build_opportunist_prompt_payload(sector_identifier, ...)`: returns the same payload as pretty-printed JSON for inspection/debugging.

### `strategist_payload.py`

Purpose: public facade for strategist payload building. Keeps the stable import surface while delegating to smaller helper modules.

Functions:
- `_serialize_filters(...)`: records the active time window and article limits used for a strategist run.
- `build_strategist_input(company_identifier, ...)`: builds the full structured strategist payload used at inference time.
- `build_strategist_prompt_payload(company_identifier, ...)`: returns the strategist payload as pretty-printed JSON for inspection/debugging.

### `strategist_payload_company.py`

Purpose: company-scope serialization plus strategist-side market-data, price-history, and trend-quality shaping.

### `strategist_payload_price.py`

Purpose: strategist price-history and trend-quality collection from yfinance-backed history frames.

### `strategist_payload_rollup.py`

Purpose: weighted rollup math for sector, industry, and company evidence summaries.

### `strategist_payload_rollup_common.py`

Purpose: shared scoring constants and helper functions used by the rollup assembly module.

### `strategist_payload_articles.py`

Purpose: signal serialization, strategist views, and ranked supporting-article assembly.

### `__init__.py`

Purpose: package marker for the `agent_builders` folder.
