# Agentic Trading Experiment

LLM-driven agentic options trading system with deterministic execution and real-time market integration.
Includes:

- a Python worker that runs the research and execution loop
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
        +--> shared runtime JSON
        |
        v
Python worker (Python Scripts/main.py)
        |
        +--> strategist stage
        +--> manager stage
        +--> deterministic option selection
        +--> Alpaca paper-trade execution
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
- `news_collector`: long-running scrape/classification refresh loop
- `ollama`: optional profile only, for local fallback

## What The Project Does Today

The implemented flow is roughly:

1. `news_collector` refreshes market/news inputs through the agent pipeline
2. `worker` chooses candidate companies from the current DB state
3. `worker` runs the strategist stage to decide `buy` vs `do_not_buy`
4. `worker` runs the manager stage to decide `call`, `put`, or `neither`
5. `worker` applies deterministic option-contract selection
6. `worker` submits option market orders through Alpaca when conditions allow
7. the runtime writes outputs and logs for the dashboard and monitoring

The worker entrypoint is [Python Scripts/main.py](Python%20Scripts/main.py), the news refresh entrypoint is [Python Scripts/news_collector_main.py](Python%20Scripts/news_collector_main.py), and the orchestrated agent stack lives under [Python Scripts/agentCallers](Python%20Scripts/agentCallers).

## Dashboard

The dashboard lives in [web_dashboard](web_dashboard) and currently shows:

- script status (`running`, `paused`, `error`, `down`)
- portfolio-history graph from `/api/portfolio-history`
- trade execution cards from `/api/trade-execution-output`

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

These are intended for the dashboard and internal monitoring.

## Runtime Outputs

The worker currently writes shared JSON outputs used by the dashboard:

- `script_status.json`
- `trade_execution_output.json`
- `selected_options_output.json`

During Docker runs, the main shared runtime paths are:

- `/shared/script_status.json`
- `/shared/trade_execution_output.json`

## Important Files

- [Python Scripts/main.py](Python%20Scripts/main.py): front-facing worker loop
- [Python Scripts/news_collector_main.py](Python%20Scripts/news_collector_main.py): scrape/classification refresh loop
- [Python Scripts/agentCallers/main.py](Python%20Scripts/agentCallers/main.py): agent-stack orchestration
- [Python Scripts/agentCallers/agent_stages/strategist.py](Python%20Scripts/agentCallers/agent_stages/strategist.py): buy/do-not-buy stage
- [Python Scripts/agentCallers/agent_stages/manager.py](Python%20Scripts/agentCallers/agent_stages/manager.py): call/put/neither stage
- [portfolio_history_service.py](portfolio_history_service.py): Alpaca portfolio-history fetch helper
- [docker-compose.yml](docker-compose.yml): local/cloud VM orchestration

## Current Status

What is already in place:

- Dockerized `web` / `api` / `worker` / `news_collector` stack
- React monitoring dashboard
- Alpaca-backed portfolio history API
- status heartbeat and trade execution outputs
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


 