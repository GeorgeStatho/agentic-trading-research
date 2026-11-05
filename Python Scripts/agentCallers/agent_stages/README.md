# agent_stages

This folder contains the model-facing stages of the current pipeline.

Each stage:

- builds a structured prompt from deterministic inputs
- calls the shared model layer from `_shared`
- parses/normalizes structured output
- returns a stable result shape for downstream code

The stages are no longer conceptually “Ollama stages only.” They now run through the shared provider layer, which can use Vertex AI or Ollama depending on `LLM_PROVIDER`.

## Stages

### `macro_news_to_sectors.py`

Purpose:

- classify recent world or US macro-news articles into affected sectors

Role in pipeline:

- early broad classification stage

Recommended model tier:

- `gemini-2.5-flash-lite`

### `sector_opportunist.py`

Purpose:

- classify evidence into sector-level impacts

Role in pipeline:

- turns macro/sector article evidence into sector opportunity signals

Recommended model tier:

- `gemini-2.5-flash-lite`

### `industry_opportunist.py`

Purpose:

- classify evidence into industry-level impacts inside a chosen sector

Recommended model tier:

- `gemini-2.5-flash-lite`

### `company_opportunist.py`

Purpose:

- classify company-linked articles into company-specific positive/negative impacts

Batching:

- sends several articles per request, because free tiers cap requests per day long before tokens
- each body is cut to `COMPANY_OPPORTUNIST_ARTICLE_BODY_CHAR_LIMIT` (default 1200), and `CLASSIFIER_BATCH_TOKENS` sets the per-request token budget
- the answer names each article. An article it leaves out is retried, not saved as "no impact"

Recommended model tier:

- `gemini-2.5-flash-lite`

### `strategist.py`

Purpose:

- decide whether one company currently looks like a `buy` or `do_not_buy`
- summarize thesis and risks from the assembled evidence package

Recommended model tier:

- `gemini-2.5-flash`

### `manager.py`

Purpose:

- decide `call`, `put`, or `neither`
- combine strategist context with live market/account context
- provide the recommendation used before deterministic option selection

Recommended model tier:

- `gemini-2.5-pro`

Implementation notes:

- `manager.py` is now the public facade for the stage.
- Prompt-building lives in `manager_prompt.py`.
- Payload shaping lives in `manager_payload_context.py` and `manager_strategist_context.py`.
- Response parsing/normalization lives in `manager_normalization.py`.
- End-to-end execution lives in `manager_runner.py`.
- `manager_context.py` remains as a thin compatibility shim for older imports.

## Notes

- Some stage files still carry older variable names like `OLLAMA_HOST` while the provider layer is being migrated.
- The manager stage is the first explicit stage already moved to the generic `get_model_client()` / `ask_llm_model()` path.
- Structured JSON output is preferred throughout the folder.
- The news classifiers walk `CLASSIFIER_FALLBACKS` when a model is out of quota, retired (404) or failing server-side (5xx). A Google 500 is retried with backoff first. A malformed request is not passed down the chain, because it would fail on every model.
- Catalyst classification (`catalyst_classifier.py`, run by `services/discovery/catalyst_classification.py`) uses the same `ask_classifier_model` call and fallback chain. It skips a batch when the model is unavailable, and stops for the cycle after three unavailable batches in a row.
