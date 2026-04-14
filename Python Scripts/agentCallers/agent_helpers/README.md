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

Purpose: gather the live market/account context used by the manager stage.

This includes:

- current stock price snapshot
- option-chain snapshot
- account buying power
- matching position state

## Current Migration Note

This folder used to be Ollama-only. The project is now in a Vertex-first migration, so the shared provider layer has moved here first while some downstream stage files still retain older `*_OLLAMA_HOST` naming.
