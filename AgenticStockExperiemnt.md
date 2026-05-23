# Agentic Stock Experiment

This document describes the current agent-driven architecture in the repository, not the earlier prototype vision.

## System Goal

The project is trying to answer one practical question:

```text
Given current market/news evidence, should this system buy an options call, buy an options put, or do nothing?
```

The design intentionally mixes:

- deterministic code for scraping, filtering, option selection, and execution
- LLM stages for classification, synthesis, and decision framing

## Current Runtime Flow

```text
Scraping + market/news data collection
        ↓
Agent pipeline candidate selection
        ↓
Strategist stage
        ↓
Manager stage
        ↓
Deterministic option selector
        ↓
Alpaca paper-trade execution
        ↓
Dashboard + API outputs
```

## Main Runtime Components

### 1. Worker loop

The main loop lives in [Python Scripts/main.py](Python%20Scripts/main.py).

Responsibilities:

- checks whether the market is open
- runs the full agent stack
- builds option-order candidates
- submits Alpaca orders when the decision and account context allow it
- writes status and trade-output files for the dashboard

It currently runs as a long-lived process.

### 2. Agent stack

The orchestrator lives in [Python Scripts/agentCallers/main.py](Python%20Scripts/agentCallers/main.py).

Current responsibilities:

- run the upstream agent/data pipeline
- collect selected company symbols
- run the strategist stage for each company
- run the manager stage for each company
- apply deterministic option selection
- package selected options for downstream execution

### 3. Strategist stage

File: [Python Scripts/agentCallers/agent_stages/strategist.py](Python%20Scripts/agentCallers/agent_stages/strategist.py)

Purpose:

- decide whether a company currently looks like a `buy` or `do_not_buy`
- summarize the thesis and main risks
- consume structured evidence instead of browsing directly

### 4. Manager stage

File: [Python Scripts/agentCallers/agent_stages/manager.py](Python%20Scripts/agentCallers/agent_stages/manager.py)

Purpose:

- decide `call`, `put`, or `neither`
- consider account context and option-market context
- return a structured recommendation used by deterministic selection logic

### 5. Deterministic option selection

The LLM does not directly choose a final contract for execution.

Instead:

- the LLM supplies direction and rationale
- deterministic code chooses a contract from the available option chain

This is a deliberate safety/control boundary.

## Model Strategy

The project now supports two LLM backends:

- `vertex`
- `ollama`

The provider abstraction lives in [Python Scripts/agentCallers/agent_helpers/shared.py](Python%20Scripts/agentCallers/agent_helpers/shared.py).

Recommended current Vertex split:

- world news: `gemini-2.5-flash-lite`
- macro news: `gemini-2.5-flash-lite`
- sector opportunist: `gemini-2.5-flash-lite`
- industry opportunist: `gemini-2.5-flash-lite`
- company opportunist: `gemini-2.5-flash-lite`
- strategist: `gemini-2.5-flash`
- manager: `gemini-2.5-pro`

This split is also captured in [.env.example](.env.example).

## Dashboard / Monitoring

The dashboard lives in [web_dashboard](web_dashboard).

It currently shows:

- worker status
- portfolio history graph
- trade execution results

The API bridge is [api.py](api.py), which serves:

- `/api/health`
- `/api/portfolio-history`
- `/api/script-status`
- `/api/trade-execution-output`

## Deployment Shape

Current Docker services:

- `web`
- `api`
- `worker`
- optional `ollama` profile

Current preferred cloud direction:

- keep `web` public
- keep `api` internal behind nginx
- use Vertex AI via Google service-account auth
- keep Ollama as an optional fallback instead of a required service

## Current Strengths

- end-to-end runtime exists
- dashboard and API are wired to the worker outputs
- option selection is partly constrained by deterministic code
- the system can already move from evidence gathering to paper-trade execution
- Vertex migration has started

## Current Gaps

- some stage files still use Ollama-oriented naming even though the shared provider layer now supports Vertex
- runtime state still relies on shared JSON files instead of more cloud-native storage
- the worker is still a forever-running loop rather than a scheduled job
- documentation and cleanup of older scripts are still ongoing

## Design Intent

This project is intentionally not “LLM decides everything.”

The intended balance is:

- deterministic code controls the hard boundaries
- LLM stages interpret evidence inside those boundaries
- execution remains auditable through saved JSON outputs, logs, and structured stage results
