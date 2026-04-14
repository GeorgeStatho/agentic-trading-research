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

### `world_news_to_sectors.py`

Purpose:

- thin world-news-specific wrapper around the macro-news-to-sectors stage

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

## Notes

- Some stage files still carry older variable names like `OLLAMA_HOST` while the provider layer is being migrated.
- The manager stage is the first explicit stage already moved to the generic `get_model_client()` / `ask_llm_model()` path.
- Structured JSON output is preferred throughout the folder.
