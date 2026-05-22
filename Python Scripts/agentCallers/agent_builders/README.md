# agent_builders

This folder contains the payload-building layer for the agent pipeline. The modules here do the prep work before a model call:

- resolve identifiers like sector, industry, and company names
- load supporting rows from the database
- normalize evidence into stable contract shapes
- enrich strategist payloads with market data, price history, and rollups
- return either Python dictionaries for runtime use or pretty-printed JSON for prompt inspection

Most functions with a leading underscore are internal helpers. The intended public entry points are the `build_*` functions plus the two company-opportunity lookup functions.

## How The Builders Fit Together

### Opportunist flow

`opportunist_payload.py` is the main entry point for sector-level opportunist prompts.

1. It resolves the requested sector.
2. It serializes filter metadata like time windows and age limits.
3. It loads sector RSS news and high-confidence macro news mapped to that sector.
4. It returns a contract-shaped payload or a JSON string version of the same payload.

### Strategist flow

`strategist_payload.py` is the public strategist facade. It coordinates several smaller helper modules.

1. It initializes the news database and builds the strategist evidence sections.
2. `strategist_payload_company.py` serializes the company block and enriches it with market data, `historical_price_data`, and `trend_quality`.
3. `strategist_payload_rollup.py` summarizes sector, industry, and company evidence into weighted rollups.
4. `strategist_payload_articles.py` builds the strategist views and the ranked supporting article lists.
5. The facade returns the final structured payload or a pretty JSON version for debugging.

## Modules

### `company_opportunity.py`

Purpose: builds the pre-strategist lookup payloads used to move from an industry or company identifier to a structured selection context.

Typical use:
- choose companies to hand off from industry exploration into later stages
- pull all stored articles linked to a specific company

Functions:
- `_find_industry(industry_identifier)`: resolves an industry by either `industry_key` or display name after ensuring the industry market data cache is present.
- `_find_company(company_identifier)`: resolves a company by ticker symbol first, then by display name.
- `_serialize_company_row(row)`: converts a raw company row into the compact `CompanySummary` payload used by downstream stages.
- `_serialize_ranked_company_row(row)`: converts a ranked company row into `RankedCompanySummary`, preserving rank and ranking type alongside the company identity fields.
- `get_industry_company_groups(industry_identifier)`: builds the grouped industry payload containing the industry summary, top companies, top growth companies, and top performing companies.
- `get_company_linked_articles(company_identifier)`: builds a company-scoped payload containing the company identity block plus all linked news articles currently stored in the database.

Notes:
- this file is mostly database lookup and normalization logic
- it does not do prompt assembly, market-history fetching, or rollup math

### `opportunist_payload.py`

Purpose: builds the normalized payload passed into the opportunist stage.

Typical use:
- gather one sector’s current RSS news plus mapped macro evidence
- produce a stable payload contract for prompt construction and tests

Functions:
- `_build_payload_filters(...)`: serializes the active request window into the `filters` block, including UTC-normalized timestamps, article age limits, macro confidence requirements, and the sector news source.
- `_build_payload_sector(sector)`: extracts only the sector identity fields needed by the opportunist payload contract.
- `build_opportunist_input(sector_identifier, ...)`: public runtime builder. Initializes the news database, resolves the sector, loads sector RSS news and high-confidence macro news, and returns the structured payload dictionary.
- `build_opportunist_prompt_payload(sector_identifier, ...)`: convenience wrapper that calls `build_opportunist_input(...)` and returns the result as pretty-printed JSON.

Notes:
- this is the opportunist equivalent of `strategist_payload.py`
- the JSON helper is mostly for debugging, prompt inspection, and contract verification

### `strategist_payload.py`

Purpose: public facade and orchestration layer for strategist payload assembly.

Why this file exists:
- preserves a stable import surface for the rest of the codebase
- keeps the top-level strategist flow readable after splitting the old large module
- re-exports helper symbols that some tests or legacy imports may still reference

Functions:
- `_serialize_filters(...)`: creates the strategist `filters` block, including UTC-normalized time bounds, confidence scope, RSS source, and article-count limits.
- `build_strategist_input(company_identifier, ...)`: main strategist runtime builder. It initializes the database, builds evidence sections, serializes the company block, assembles views, computes the opportunist rollup, and attaches supporting articles.
- `build_strategist_prompt_payload(company_identifier, ...)`: calls `build_strategist_input(...)` and returns pretty-printed JSON for prompt debugging and inspection.

Key collaborators:
- `strategist_payload_company.py` for `company`
- `strategist_payload_rollup.py` for `opportunist_rollup`
- `strategist_payload_articles.py` for `views` and `supporting_articles`

### `strategist_payload_company.py`

Purpose: owns company-scope serialization for strategist payloads.

This module is responsible for taking the company identity returned by strategist evidence building and turning it into the richer `company` block used by the strategist prompt.

Functions:
- `_serialize_company_scope(company)`: main helper for the strategist `company` block. It combines stable identity fields, rating and market-weight fields, parsed `market_data`, strategist-side `historical_price_data`, and `trend_quality`.
- `_deserialize_company_raw_json(raw_json)`: safely converts the company `raw_json` field from the database into a dictionary. Invalid JSON or non-dict payloads degrade to `{}` instead of raising.
- `_get_company_market_record(company)`: finds the most relevant full company record from the database, first by `company_id` and then by ticker symbol as a fallback.

Dependency note:
- this module calls `_build_company_price_context(...)` from `strategist_payload_price.py` to attach price history and trend-quality data beside the company metadata

### `strategist_payload_price.py`

Purpose: collects strategist-side historical price snapshots and computes trend-quality metrics from yfinance history.

This is the main market-data utility module for strategist payload building.

Constants:
- `DEFAULT_TREND_QUALITY_RISK_FREE_RATE`: currently `0.0`, used when computing Sharpe-style excess returns.
- `DEFAULT_TREND_QUALITY_TRADING_PERIODS_PER_YEAR`: currently `252`, used to annualize trend-quality Sharpe values.
- `HISTORICAL_PERIOD_CONFIG`: defines which price-history windows are fetched for `historical_price_data`, their bar intervals, and how many recent rows to sample into the payload.
- `TREND_QUALITY_WINDOW_CONFIG`: defines which history windows feed `trend_quality` metrics like `5d_sharpe`, `1m_sharpe`, and `3m_sharpe`.

Functions:
- `_safe_float(value)`: converts numeric-like values to `float`, returning `None` for blanks, invalid values, or `NaN`.
- `_safe_int(value)`: converts numeric-like values to `int`, returning `None` when conversion is not possible.
- `_serialize_timestamp(value)`: converts index timestamps or other values into string form for payload output.
- `_empty_historical_snapshot(...)`: returns a consistent empty `HistoricalPriceSnapshot` payload when data is missing or an error occurs.
- `_empty_trend_quality_payload(...)`: returns the default `TrendQualityPayload` shell with all Sharpe and return fields present.
- `_summarize_history_frame(...)`: converts a yfinance history frame into one compact snapshot with first/last prices, percent change, high/low, total volume, and sampled recent points.
- `_build_close_returns(close_values)`: converts a close-price series into period-over-period returns while skipping invalid or non-positive prices.
- `_summarize_trend_quality_from_closes(...)`: computes the annualized Sharpe-style value and total return percent from a normalized close series.
- `_extract_close_values_from_frame(frame)`: pulls valid close values from a history frame for trend-quality calculations.
- `_summarize_trend_quality_frame(...)`: wrapper that extracts closes from a frame and delegates to `_summarize_trend_quality_from_closes(...)`.
- `_fetch_ticker_history_frame(ticker, symbol, *, period, interval)`: fetches one yfinance history frame through the shared request handler.
- `_build_company_price_context(symbol)`: main internal orchestrator for this module. It fetches all configured history windows, caches frames by `(period, interval)`, builds `historical_price_data`, and computes the `trend_quality` block.
- `_build_company_historical_price_data(symbol)`: compatibility helper that returns only the historical-price portion of `_build_company_price_context(...)`.

Operational notes:
- the module degrades gracefully when yfinance is unavailable, the ticker symbol is missing, or a fetch fails
- the short-term `5d` trend-quality metrics are intentionally sourced from `5d` `1h` history so they line up with the strategist’s short-horizon price context

### `strategist_payload_rollup_common.py`

Purpose: shared constants and weighting helpers used by strategist rollup construction.

This module centralizes the scoring vocabulary so rollup math stays consistent across sector, industry, and company summaries.

Constants:
- `ROLLUP_DIRECTIONS`: allowed impact directions such as `positive`, `negative`, `mixed`, and `neutral`
- `ROLLUP_MATERIALITY`: allowed materiality buckets
- `ROLLUP_HORIZONS`: allowed time-horizon buckets
- `ROLLUP_EFFECT_TYPES`: allowed effect-type buckets
- `ROLLUP_RELATIVE_POSITIONING`: peer-comparison buckets used for company evidence
- `MATERIALITY_WEIGHTS`: weight multipliers for high/medium/low materiality
- `MAGNITUDE_WEIGHTS`: weight multipliers for impact magnitude
- `EFFECT_TYPE_WEIGHTS`: weight multipliers for direct versus indirect effects
- `CONFIDENCE_WEIGHTS`: weight multipliers for signal confidence

Functions:
- `_round_rollup_metric(value)`: rounds rollup metrics to a stable precision for payload output.
- `_normalize_reason_key(reason)`: canonicalizes free-text reasons so similar reason strings group together.
- `_safe_int(value)`: safe integer conversion helper used when reading article ids.
- `_get_signal_weight(item)`: computes the combined weight for one evidence item based on materiality, magnitude, effect type, and confidence.
- `_build_empty_count_map(keys)`: creates zeroed count dictionaries for the configured bucket sets.
- `_build_empty_weight_map(keys)`: creates zeroed weighted-score dictionaries for the configured bucket sets.
- `_pick_dominant_bucket(weighted_totals, *, default)`: chooses the winning bucket for a weighted distribution, falling back to `default` when totals are empty or too close to call.
- `_build_top_reasons(items, ...)`: aggregates repeated reasons across evidence items, scores them, and returns the top-ranked reason summaries.

### `strategist_payload_rollup.py`

Purpose: turns raw strategist evidence items into the compact rollup summaries attached to the strategist payload.

This module is where the evidence becomes a decision-oriented summary instead of a flat article list.

Functions:
- `_build_rollup_section(items, *, include_relative_positioning=False)`: builds a rollup summary for one list of evidence items. It counts directions, materiality, horizons, and effect types; computes weighted totals; detects contradictions; and ranks the most important reasons.
- `_build_grouped_rollups(items, *, subject_id_key, subject_key_key, subject_name_key)`: groups evidence by a subject key such as industry, builds a rollup for each group, and sorts the results by strength.
- `_build_opportunist_rollup(evidence)`: assembles the full strategist `opportunist_rollup` block by producing sector, industry, and company rollups plus top supportive and risky industries.

Output focus:
- summary counts for each evidence dimension
- weighted directional scores
- contradiction flags
- top reasons and top positive/negative/direct/indirect reasons
- grouped industry summaries and company relative-positioning summaries

### `strategist_payload_articles.py`

Purpose: serializes strategist evidence into prompt-friendly views and ranked supporting article lists.

This module sits closest to the prompt contract. It decides how article-linked evidence becomes strategist view `agent_conclusions`, article summaries, and full-article attachments.

Functions:
- `_serialize_signal(item, *, layer)`: converts one evidence item into the compact signal record stored in strategist views and supporting-article `agent_signals`.
- `_build_view(...)`: packages one evidence layer such as `macro_view` or `company_view` into the view structure expected by the strategist payload.
- `_upsert_supporting_article(...)`: deduplicates articles by `article_id`, merges layer membership, and optionally appends the serialized agent signal for that article-layer pair.
- `_build_supporting_articles(evidence, *, summary_article_limit, full_article_limit)`: builds the final ranked `supporting_articles` block, including summary articles and a smaller set of full-body articles.

Ranking behavior:
- articles are deduplicated across macro, sector, industry, company, and RSS evidence
- higher-confidence and more frequently referenced articles rank above weaker or isolated ones
- only articles with a non-empty body make it into the `full_articles` list

### `__init__.py`

Purpose: package marker for the `agent_builders` folder.
