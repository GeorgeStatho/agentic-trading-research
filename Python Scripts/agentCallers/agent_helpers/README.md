# agent_helpers

This folder contains the shared lower-level logic used by the model stages and the pipeline.

The modules here handle:

- LLM provider access
- JSON extraction and normalization helpers
- time-window filtering
- evidence assembly
- live market/account context loading
- persistence helpers for stage outputs

## Most Important Files

### `shared.py`

Purpose: shared LLM + utility layer used across the project.

Current role:

- supports both `vertex` and `ollama`
- exposes the common provider abstraction used by the stages
- keeps backward-compatible Ollama wrapper names during the migration

Key helpers:

- `get_model_client(label=None)`: returns the current provider client based on `LLM_PROVIDER`
- `ask_llm_model(...)`: sends a structured request through the active provider
- `get_ollama_client(...)` / `ask_ollama_model(...)`: compatibility wrappers for older stage code
- `parse_published_at(...)`
- `normalize_time_window(...)`
- `published_at_in_window(...)`
- `estimate_tokens(...)`
- `build_token_limited_batches(...)`
- `extract_json_value(...)`

### `macro_news.py`

Purpose: load and normalize recent macro-news rows used by the macro/world-news classification stages.

### `opportunist_payload.py`

Purpose: assemble sector RSS and macro evidence into the payload used by opportunist stages.

### `sector_opportunist.py`

Purpose: helper logic for sector-level impact extraction, normalization, and persistence.

### `industry_opportunist.py`

Purpose: helper logic for industry-level impact extraction, normalization, and persistence.

### `company_opportunist.py`

Purpose: helper logic for company-level impact extraction, normalization, and persistence.

### `strategist.py`

Purpose: assemble the multi-layer evidence package used by the strategist stage.

### `manager.py`

Purpose: public façade for assembling the full manager payload.

Use these functions:

- `build_manager_input(company_identifier, ...)`
  Use when you want the full strategist evidence package plus live market data.
- `test_market_context(company_identifier, ...)`
  Use when you want a diagnostics-first snapshot to verify Alpaca data before running the manager LLM stage.

Internal SOLID split:

- `manager.py` now owns orchestration only
- `market_context/` owns Alpaca client wiring, stock snapshots, option snapshots, and account-state assembly

This includes:

- current stock price snapshot
- option-chain snapshot
- account buying power
- matching position state

### `market_context/`

Purpose: isolated market/account service layer for the manager stage.

Use:

- `build_market_context(company, ...)`
  Accepts the company block from a strategist payload and returns a JSON-safe market context payload.
- See `market_context/README.md` for the package-level map.

How it works currently:

- `market_context/__init__.py` is now a thin façade.
- It keeps the stable public entrypoint and re-exports a few internal wrappers used by tests.
- The real implementation is split across focused helper modules in `market_context/`.

Current module split:

- `market_context/common.py`
  Shared constants, Alpaca/yfinance imports, client bootstrapping, and low-level helpers like `_safe_float(...)`, `_get_field(...)`, and `_serialize_scalar(...)`.
- `market_context/equities.py`
  Current stock price snapshot, market-index snapshots, sector ETF lookup, and reference-stock-price resolution.
- `market_context/underlying.py`
  Alpaca stock-bar history loading plus realized/historical volatility for the underlying.
- `market_context/option_chain.py`
  Option contract lookup, chain snapshot normalization, subset selection near spot, and option-market payload assembly.
- `market_context/iv_history.py`
  Persisted IV history, DTE-bucket IV percentile logic, and contract-level IV percentile annotation.
- `market_context/account.py`
  Account buying power, matching stock/option positions, and position serialization.

Top-level data flow:

1. `build_market_context(company, ...)` starts in `market_context/__init__.py`.
2. `_build_current_stock_price_snapshot(...)` loads the live stock quote/trade snapshot.
3. `_get_reference_stock_price_from_snapshot(...)` chooses the price anchor used for option filtering and ranking.
4. `_build_underlying_price_history_snapshot(...)` loads Alpaca daily bars and computes `historical_volatility_20d` and `historical_volatility_60d`.
5. `_build_option_market_snapshot(...)` loads option contracts plus chain snapshots and builds the option-market block.
6. `_build_account_state(...)` loads buying power and any matching stock/option positions for the company.
7. The combined JSON-safe payload is returned to the manager stage.

Returned payload shape:

- `current_stock_price`
  Live stock quote/trade snapshot.
- `underlying_price_history`
  Daily-bar history summary with realized volatility.
- `market_indices`
  Broad market context such as S&P 500, Dow, and VIX.
- `sector_etf`
  Sector ETF snapshot matched from `Data/sector_etfs.json`.
- `option_market`
  Option chain subset, selection filters, volatility summary, and diagnostics.
- `account_state`
  Buying power plus any existing company-related positions.

How volatility enters the payload:

- Contract-level `implied_volatility` comes from Alpaca option chain snapshots.
- Underlying `historical_volatility_20d` and `historical_volatility_60d` are computed from Alpaca stock bars.
- `summarize_option_iv(...)` from `volatility.py` builds the base `option_market.volatility_summary`.
- `_enrich_with_alpaca_iv_percentiles(...)` adds persisted percentile context and writes IV history to `OPTION_IV_HISTORY_PATH`.

Current IV-percentile behavior:

- IV history is persisted by underlying symbol.
- Chain-wide `atm_iv_percentile` is still tracked as a market-wide context signal.
- More importantly for contract selection, the code now computes IV summaries by DTE bucket:
  `1_3`, `4_7`, `8_14`, `15_30`, `31_45`, `46_60`.
- Contracts are annotated with:
  - `days_to_expiration`
  - `dte_bucket`
  - `iv_percentile`
  - `dte_bucket_iv_percentile`
  - `iv_percentile_source`
- Bucket percentiles are only used when enough history exists for that bucket.

Why `market_context/__init__.py` still has some wrappers:

- Tests currently patch `agent_helpers.market_context` directly.
- To preserve that patch surface, the façade still exposes wrapper functions around the IV-history helpers, especially for:
  - `OPTION_IV_HISTORY_PATH`
  - `OPTION_IV_HISTORY_MAX_ENTRIES`
  - `OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES`
  - `datetime`
- This lets the implementation be modular without forcing all tests to patch the private module files directly.

Operational notes:

- Alpaca is the primary provider for live stock, option, and account data.
- `yfinance` is still used for market indices and sector ETF snapshots.
- `python -m agent_helpers.market_context` can still be used as a stock-price smoke test.

When to edit which file:

- Change quote/trade or sector/index behavior in `market_context/equities.py`.
- Change underlying close-history or HV behavior in `market_context/underlying.py`.
- Change contract filtering, snapshot assembly, or option subset selection in `market_context/option_chain.py`.
- Change persisted IV history, DTE-bucket percentile rules, or contract IV-percentile annotation in `market_context/iv_history.py`.
- Change buying power / position-state behavior in `market_context/account.py`.
- Only edit `market_context/__init__.py` when you need to change the public façade, orchestration, or compatibility wrappers.

### `opportunist_support.py`

Purpose: shared article merge, sort, and processed-id filtering helpers used by the sector/industry/company opportunist helpers.

Use:

- `sort_articles_by_recency(...)`
- `filter_unprocessed_articles(...)`
- `extract_impacts_from_payload(...)`

These exist so each opportunist helper can focus on stage-specific validation and persistence rules instead of repeating common article handling logic.

### `deterministic_option_selector/`

Purpose: deterministic contract-selection façade used after the manager stage decides direction.

Use:

- `apply_deterministic_option_selection(manager_result)`
  Use when the manager has already decided `call`, `put`, or `neither` and you want to attach a concrete option contract using rule-based filtering instead of an LLM.
- See `deterministic_option_selector/README.md` for the package-level map.

How it works currently:

- `deterministic_option_selector/__init__.py` is now a thin façade plus final orchestration layer.
- It keeps the stable public entrypoint and the patch surface used by tests.
- The actual selector logic is split across focused helper modules in `deterministic_option_selector/`.

Current module split:

- `deterministic_option_selector/config.py`
  Selector env/config loading and all mode tunables.
- `deterministic_option_selector/normalize.py`
  Normalization helpers for decisions, confidence, booleans, option IDs, and selection guardrails.
- `deterministic_option_selector/market.py`
  Read-only helpers for contract quotes, greeks, DTE, market-context access, IV percentile lookup, and reference-stock-price resolution.
- `deterministic_option_selector/volatility.py`
  Volatility scoring logic: IV percentile, IV vs HV, term structure warning, and combined volatility assessment.
- `deterministic_option_selector/filters.py`
  Shared DTE/liquidity/OTM filters, spread/theta helpers, and common ranking helpers.
- `deterministic_option_selector/debug.py`
  Contract debug snapshot helpers and human-readable rejection/debug payload assembly.
- `deterministic_option_selector/simple.py`
  Simple selector mode implementation.
- `deterministic_option_selector/hybrid.py`
  Hybrid selector mode implementation.
- `deterministic_option_selector/greeks.py`
  Greeks/swing selector mode implementation.

Top-level selector flow:

1. `apply_deterministic_option_selection(...)` starts in `deterministic_option_selector/__init__.py`.
2. The façade normalizes the manager recommendation and checks confidence/strategist guardrails.
3. It dispatches into the requested selector mode:
   - `simple`
   - `hybrid`
   - `greeks`
4. The selected contract is then passed through volatility scoring.
5. Volatility can downgrade `confidence_after_volatility` and, if needed, reject the contract.
6. The final recommendation is enriched with:
   - `selected_option_id`
   - `selected_expiration_date`
   - `selected_strike_price`
   - `selected_option_source`
   - `selection_debug`
   - `confidence_after_volatility`

Current selector responsibilities:

- enforce contract-level viability after the manager has chosen direction
- use DTE buckets and OTM-distance rules
- enforce liquidity and spread checks
- score contract quality deterministically
- incorporate volatility penalties without asking the LLM to choose a contract
- provide a rich `selection_debug` payload for diagnostics

Current volatility behavior inside the selector:

- reads contract-level `implied_volatility`
- reads underlying historical volatility from `market_context`
- reads DTE-bucket IV percentile and term-structure context from `option_market.volatility_summary`
- builds a per-contract volatility assessment with:
  - `iv_percentile`
  - `iv_hv`
  - `term_structure`
  - `total_penalty_points`
  - `confidence_penalty_steps`

Mode overview:

- `simple`
  Cheapest path. Prefers target-OTM contracts and can fall back to side-correct contracts.
- `hybrid`
  Short-DTE / liquidity-aware mode with spread, OI, delta, theta, gamma, and OTM-distance filters.
- `greeks`
  Stricter swing-like mode with tighter DTE, delta, gamma, spread, theta, and OI requirements.

Why `deterministic_option_selector/__init__.py` still keeps orchestration:

- callers already import `apply_deterministic_option_selection(...)` from this file
- tests currently patch `agent_helpers.deterministic_option_selector.OPTION_SELECTOR_MODE`
- keeping the façade as the orchestration layer preserves that behavior while still allowing the internal logic to be modular

Operational note:

- `recommendation["confidence"]` now stays as the manager’s original confidence
- `recommendation["confidence_after_volatility"]` carries the selector’s volatility-adjusted view
- this makes it easier to tell what came from the manager versus what was changed by deterministic contract-level risk controls

When to edit which file:

- Change selector mode/env/tunables in `deterministic_option_selector/config.py`.
- Change normalization or confidence semantics in `deterministic_option_selector/normalize.py`.
- Change quote/DTE/contract market-context lookups in `deterministic_option_selector/market.py`.
- Change IV percentile / IV-HV / term-structure scoring in `deterministic_option_selector/volatility.py`.
- Change shared contract filters or ranking behavior in `deterministic_option_selector/filters.py`.
- Change selector debug payloads in `deterministic_option_selector/debug.py`.
- Change simple mode selection in `deterministic_option_selector/simple.py`.
- Change hybrid mode selection in `deterministic_option_selector/hybrid.py`.
- Change greeks mode selection in `deterministic_option_selector/greeks.py`.
- Only edit `deterministic_option_selector/__init__.py` when you need to change the public façade, mode dispatch, or final recommendation orchestration.

## Quick Examples

Build manager input:

```python
from agent_helpers.manager import build_manager_input

payload = build_manager_input(
    "AAPL",
    start_time=None,
    end_time=None,
    max_age_days=5,
    summary_article_limit=20,
    full_article_limit=5,
    option_expiration_date=None,
    option_expiration_date_gte=None,
    option_expiration_date_lte=None,
    option_strike_price_gte=None,
    option_strike_price_lte=None,
    option_contract_limit_per_type=6,
)
```

Apply deterministic contract selection after the manager stage:

```python
from agent_helpers.deterministic_option_selector import apply_deterministic_option_selection

enriched_result = apply_deterministic_option_selection(manager_result)
```

## Current Migration Note

This folder used to be Ollama-only. The project is now in a Vertex-first migration, so the shared provider layer has moved here first while some downstream stage files still retain older `*_OLLAMA_HOST` naming.
