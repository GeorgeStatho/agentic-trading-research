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

Purpose: build the final strategist input payload by merging macro, sector, industry, company, and raw article evidence for one company.

Functions:
- `_serialize_filters(...)`: records the active time window and article limits used for a strategist run.
- `_serialize_company_scope(company)`: serializes the selected company plus market metadata.
- `_deserialize_company_raw_json(raw_json)`: safely parses stored market-data JSON.
- `_get_company_market_record(company)`: looks up the saved company market-data row for the target company.
- `_serialize_signal(item, layer=...)`: converts one saved impact row into a strategist signal record.
- `_build_view(...)`: builds one strategist view section such as `macro_view` or `company_view`.
- `_upsert_supporting_article(...)`: merges article evidence from multiple layers into one article-centered record.
- `_build_supporting_articles(...)`: ranks evidence articles and produces the strategist `article_summaries` and `full_articles` sections.
- `build_strategist_input(company_identifier, ...)`: builds the full structured strategist payload used at inference time.
- `build_strategist_prompt_payload(company_identifier, ...)`: returns the strategist payload as pretty-printed JSON for inspection/debugging.

### `__init__.py`

Purpose: package marker for the `agent_builders` folder.
