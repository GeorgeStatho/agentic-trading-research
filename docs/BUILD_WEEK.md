# OpenAI Build Week (July 2026)

## What changed during Build Week

From July 20–21, 2026, the project was meaningfully extended from a trading
workflow into an evidence-first, auditable decision system. The Build Week work
added:

- an interactive **Evidence Exposure Map** that links a news article to its
  sector, industry, company, and current paper-option exposure;
- grounded model outputs with persisted inputs, outputs, prompt versions, and
  timestamps for later review;
- a read-only **Decision Autopsy Ledger** and **Decision Expectancy** views for
  tracing what the system knew, decided, constrained, and learned;
- historical option bid/ask coverage, explicit no-fill and coverage-gap
  outcomes, and immutable backtest runs;
- portfolio-level entry controls, an evidence-confirmed trend-continuation
  gate, and a deterministic market-regime gate; and
- a seeded **Demo Mode** that shows the evidence-to-risk-to-review workflow
  without live market data, model credentials, or trade execution.

The full feature and validation notes are in
the project notes (not in the public demo).

## Run and demo

For a local instance, follow the [Quick Start](../README.md#quick-start) instructions and
open `http://localhost` after the Docker services are running. For a reliable
review without broker, market-data, or model credentials, use **Demo Mode** in
the dashboard or open `#/exposure-map?demo=1`. Demo Mode uses seeded fictional
data and does not place orders.

The public deployment, when available, is
`https://dashboard.example.com/#/exposure-map?demo=1`.

## Model providers and GPT-5.6 Terra

The project supports OpenAI, Vertex AI, and Ollama through a shared provider
abstraction. The Exposure Map uses the model configured for the active
provider. When `LLM_PROVIDER=openai`, it defaults to `gpt-5.6-terra`; Vertex
and Ollama use their own configured defaults. `EXPOSURE_MAP_MODEL` can override
the selected default for a specific environment.

```bash
# Optional OpenAI-backed Exposure Map configuration
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
EXPOSURE_MAP_MODEL=gpt-5.6-terra
```

For Exposure Map analysis, model output is limited to grounded, structured
analysis. Python validates every referenced identifier and computes risk
deterministically; the model does not independently set risk limits or override
execution controls.

## How Codex contributed

Codex was used throughout the Build Week extension to implement and review the
Flask APIs, SQLite persistence, provider configuration, React/React Flow
dashboard experience, Demo Mode, historical-backtest workflow, deterministic
controls, and focused automated tests. Product and engineering decisions—such
as persisting immutable decision snapshots, rejecting ungrounded references,
and failing closed on missing market evidence—were reviewed against the
project's auditability and risk-control goals.
