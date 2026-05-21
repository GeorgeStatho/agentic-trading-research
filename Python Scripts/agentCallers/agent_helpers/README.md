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
- `market_context.py` owns Alpaca client wiring, stock snapshots, option snapshots, and account-state assembly

This includes:

- current stock price snapshot
- option-chain snapshot
- account buying power
- matching position state

### `market_context.py`

Purpose: isolated market/account service layer for the manager stage.

Use:

- `build_market_context(company, ...)`
  Accepts the company block from a strategist payload and returns a JSON-safe market context payload.

How it works currently:

- `market_context.py` is now a thin façade.
- It keeps the stable public entrypoint and re-exports a few internal wrappers used by tests.
- The real implementation is split across focused helper modules in this folder.

Current module split:

- `_market_context_common.py`
  Shared constants, Alpaca/yfinance imports, client bootstrapping, and low-level helpers like `_safe_float(...)`, `_get_field(...)`, and `_serialize_scalar(...)`.
- `_market_context_equities.py`
  Current stock price snapshot, market-index snapshots, sector ETF lookup, and reference-stock-price resolution.
- `_market_context_underlying.py`
  Alpaca stock-bar history loading plus realized/historical volatility for the underlying.
- `_market_context_option_chain.py`
  Option contract lookup, chain snapshot normalization, subset selection near spot, and option-market payload assembly.
- `_market_context_iv_history.py`
  Persisted IV history, DTE-bucket IV percentile logic, and contract-level IV percentile annotation.
- `_market_context_account.py`
  Account buying power, matching stock/option positions, and position serialization.

Top-level data flow:

1. `build_market_context(company, ...)` starts in `market_context.py`.
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

Why `market_context.py` still has some wrappers:

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
- `market_context.py` can still be run directly as a stock-price smoke test.

When to edit which file:

- Change quote/trade or sector/index behavior in `_market_context_equities.py`.
- Change underlying close-history or HV behavior in `_market_context_underlying.py`.
- Change contract filtering, snapshot assembly, or option subset selection in `_market_context_option_chain.py`.
- Change persisted IV history, DTE-bucket percentile rules, or contract IV-percentile annotation in `_market_context_iv_history.py`.
- Change buying power / position-state behavior in `_market_context_account.py`.
- Only edit `market_context.py` when you need to change the public façade, orchestration, or compatibility wrappers.

### `opportunist_support.py`

Purpose: shared article merge, sort, and processed-id filtering helpers used by the sector/industry/company opportunist helpers.

Use:

- `sort_articles_by_recency(...)`
- `filter_unprocessed_articles(...)`
- `extract_impacts_from_payload(...)`

These exist so each opportunist helper can focus on stage-specific validation and persistence rules instead of repeating common article handling logic.

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
