# Agentic Trading Experiment

LLM-driven agentic options trading system with deterministic execution and real-time market integration.
Includes:

- a Python worker that runs the research and execution loop
- a dedicated option manager that monitors and closes existing option positions
- a Flask API that exposes portfolio history, trade output, and script status
- a React dashboard for monitoring the system
- a model layer that can use either Vertex AI or Ollama

It is still an experiment, not production trading software.

## Current Architecture

The current runtime stack is:

```text
React dashboard (web_dashboard)
        |
        v
Flask API (api.py)
        |
        +--> Alpaca portfolio history
        +--> SQLite-backed executed trade journal
        +--> shared runtime JSON
        |
        v
Python worker (Python Scripts/main.py)
        |
        +--> strategist stage
        +--> manager stage
        +--> deterministic option selection
        +--> Friday entry guard for new option buys
        +--> Alpaca paper-trade execution
        |
        v
Dedicated option manager (Python Scripts/option_manager_main.py)
        |
        +--> DTE-aware option position management
        +--> deterministic exits / auto-close rules
        |
        v
Persisted market/news DB state
        ^
        |
News collector (Python Scripts/news_collector_main.py)
        |
        +--> scraping + market/news collection
        +--> sector/industry/company classification
```

Docker Compose currently runs:

- `web`: nginx + built React frontend
- `api`: Flask API
- `worker`: long-running strategist/manager/trading loop using existing DB data
- `option_manager`: long-running existing-position management loop for options
- `news_collector`: long-running scrape/classification refresh loop
- `ollama`: optional profile only, for local fallback

## What The Project Does Today

The implemented flow is roughly:

1. `news_collector` refreshes market/news inputs through the agent pipeline
2. `worker` chooses candidate companies from the current DB state
3. `worker` runs the strategist stage to decide `buy` vs `do_not_buy`
4. `worker` runs the manager stage to decide `call`, `put`, or `neither`
5. `worker` applies deterministic option-contract selection
6. `worker` skips fresh option entries on Fridays in `America/New_York` market time
7. `worker` submits option market orders through Alpaca when conditions allow
8. successful live option submissions are recorded into SQLite for durable trade history
9. `option_manager` monitors open option positions and can close them using DTE-aware exit thresholds
10. the runtime writes outputs and logs for the dashboard and monitoring

The worker entrypoint is [Python Scripts/main.py](Python%20Scripts/main.py), the dedicated position-manager entrypoint is [Python Scripts/option_manager_main.py](Python%20Scripts/option_manager_main.py), the news refresh entrypoint is [Python Scripts/news_collector_main.py](Python%20Scripts/news_collector_main.py), and the orchestrated agent stack lives under [Python Scripts/agentCallers](Python%20Scripts/agentCallers).

## Dashboard

The dashboard lives in [web_dashboard](web_dashboard) and currently shows:

- script status (`running`, `paused`, `error`, `down`)
- combined worker + option-manager status via the API
- portfolio-history graph from `/api/portfolio-history`
- executed trade cards from the SQLite-backed `/api/executed-trades` endpoint
- option position management output from the dedicated option manager

The main frontend files are:

- [web_dashboard/src/App.tsx](web_dashboard/src/App.tsx)
- [web_dashboard/src/Graph.tsx](web_dashboard/src/Graph.tsx)
- [web_dashboard/src/orderCard.tsx](web_dashboard/src/orderCard.tsx)
- [web_dashboard/src/scriptStatus.tsx](web_dashboard/src/scriptStatus.tsx)

## Model Providers

The repo now supports two model backends behind a shared helper:

- `vertex`: recommended for cloud deployment
- `ollama`: optional local fallback

The provider abstraction lives in [Python Scripts/agentCallers/agent_helpers/shared.py](Python%20Scripts/agentCallers/agent_helpers/shared.py).

### Recommended Vertex split

The current recommended stage/model split is captured in [.env.example](.env.example):

- `WORLD_NEWS_MODEL=gemini-2.5-flash-lite`
- `MACRO_NEWS_MODEL=gemini-2.5-flash-lite`
- `SECTOR_OPPURUNTIST_MODEL=gemini-2.5-flash-lite`
- `INDUSTRY_OPPURUNTINST_MODEL=gemini-2.5-flash-lite`
- `COMPANY_OPPURUNTIST_MODEL=gemini-2.5-flash-lite`
- `STRATEGIST_MODEL=gemini-2.5-flash`
- `MANAGER_MODEL=gemini-2.5-pro`

## Quick Start

### 1. Create environment config

Use the template:

```bash
cp .env.example .env
```

Then fill in at minimum:

- `PUBLIC_KEY`
- `PRIVATE_KEY`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`

### 2. Run with Docker

Default cloud-friendly startup:

```bash
docker compose up --build
```

If you want the local Ollama fallback too:

```bash
docker compose --profile ollama up --build
```

### 3. Open the dashboard

By default:

```text
http://localhost:8080
```

## Vertex AI Authentication

The current Docker configuration is set up for Google Application Default Credentials rather than a checked-in key file.

Recommended cloud setup:

- attach a service account to the VM / Cloud Run workload
- grant it `roles/aiplatform.user`
- set:

```env
LLM_PROVIDER=vertex
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
```

On Google Cloud, the containers should authenticate automatically through the attached service account.

## API Endpoints

The Flask service in [api.py](api.py) exposes:

- `GET /api/health`
- `GET /api/portfolio-history`
- `GET /api/script-status`
- `GET /api/trade-execution-output`
- `GET /api/executed-trades`

These are intended for the dashboard and internal monitoring.

## Runtime Outputs

The runtime currently persists trade data in two ways:

- SQLite table `option_trade_executions` for durable executed-trade history used by `/api/executed-trades`
- shared JSON outputs for compatibility and dashboard/runtime state

The shared JSON outputs currently written are:

- `script_status.json`
- `option_manager_status.json`
- `trade_execution_output.json`
- `selected_options_output.json`
- `option_position_management_output.json`

During Docker runs, the main shared runtime paths are:

- `/shared/script_status.json`
- `/shared/option_manager_status.json`
- `/shared/trade_execution_output.json`
- `/shared/option_position_management_output.json`

## Important Files

- [Python Scripts/main.py](Python%20Scripts/main.py): front-facing worker loop
- [Python Scripts/option_manager_main.py](Python%20Scripts/option_manager_main.py): dedicated option-position management loop
- [Python Scripts/services/front_main_application.py](Python%20Scripts/services/front_main_application.py): scheduled worker loop, Friday entry guard, and position-management orchestration
- [Python Scripts/services/trade_journal.py](Python%20Scripts/services/trade_journal.py): records successful live option submissions into SQLite
- [Python Scripts/trading_support](Python%20Scripts/trading_support): split trading helpers for client setup, account diagnostics, option management, and stock/order helpers
- [Data/market_schema.sql](Data/market_schema.sql): market-side SQLite schema, including the executed-trade journal table
- [Data/market_db.py](Data/market_db.py): market-side DB writes and executed-trade query helpers
- [Python Scripts/news_collector_main.py](Python%20Scripts/news_collector_main.py): scrape/classification refresh loop
- [Python Scripts/agentCallers/main.py](Python%20Scripts/agentCallers/main.py): agent-stack orchestration
- [Python Scripts/agentCallers/agent_stages/strategist.py](Python%20Scripts/agentCallers/agent_stages/strategist.py): buy/do-not-buy stage
- [Python Scripts/agentCallers/agent_stages/manager.py](Python%20Scripts/agentCallers/agent_stages/manager.py): call/put/neither stage
- [portfolio_history_service.py](portfolio_history_service.py): Alpaca portfolio-history fetch helper
- [docker-compose.yml](docker-compose.yml): local/cloud VM orchestration

## Current Status

What is already in place:

- Dockerized `web` / `api` / `worker` / `news_collector` stack
- dedicated `option_manager` runtime for existing position supervision
- React monitoring dashboard
- Alpaca-backed portfolio history API
- status heartbeat and trade execution outputs
- SQLite-backed executed trade journal plus `/api/executed-trades`
- option position management output and option-manager status reporting
- Friday block for new option entries in the worker pipeline
- refactored trading helpers under `Python Scripts/trading_support`
- provider abstraction for Vertex AI vs Ollama
- first-stage Vertex migration

What is still being finished:

- converting the remaining stage files from Ollama-specific naming to the generic provider interface
- moving some shared JSON runtime state to more cloud-native storage
- hardening deployment for Cloud Run jobs/services
- polishing documentation and cleanup of older prototype scripts

## Safety Notes

- This project can place paper trades and contains real trading logic.
- Do not treat it as financial advice.
- Review and rotate credentials if they have ever been exposed during development.

## Contact / Collaboration

I’m actively developing this agentic trading system and exploring improvements in:

- risk management strategies for options trading  
- LLM-driven decision systems  
- deterministic contract selection under real-world constraints  

If you’re working on similar systems, have feedback, or are interested in collaborating, feel free to reach out:

- GitHub: https://github.com/GeorgeStatho  
- LinkedIn: www.linkedin.com/in/george-stathopoulos-0601b1229  
- Email: georgestathopou [at] gmail [dot] com  


 
