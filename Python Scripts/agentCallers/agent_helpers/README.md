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
