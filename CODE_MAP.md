# Code Syntax Tree And Map

This file is a high-level map of the repository as it exists now.
It is meant to answer two questions quickly:

1. What is in this repo?
2. How do the main pieces connect?


## 1. Syntax Tree

```text
Stock-trading-experiment/
├── README.md
├── AgenticStockExperiemnt.md
├── requirements.txt
├── config/
│   └── Modelfile
├── Data/
│   ├── db.py
│   ├── db_common.py
│   ├── market_db.py
│   ├── news_db.py
│   ├── market_schema.sql
│   ├── news_schema.sql
│   ├── queryDB.py
│   ├── macro_events.json
│   ├── logs/
│   └── db_helpers/
│       ├── __init__.py
│       ├── common.py
│       ├── market.py
│       ├── news.py
│       └── queries.py
├── Python Scripts/
│   ├── main.py
│   ├── MarketData.py
│   ├── Trading.py
│   ├── trading_support/
│   │   ├── __init__.py
│   │   ├── account.py
│   │   ├── clients.py
│   │   ├── option_contracts.py
│   │   ├── option_positions.py
│   │   ├── stock_orders.py
│   │   └── utils.py
│   ├── Companies/
│   │   └── Company.py
│   ├── WritingToDataBase/
│   │   └── stringtoJson.py
│   ├── Webscraping/
│   │   ├── README.md
│   │   ├── article_scraper.py
│   │   ├── article_extraction.py
│   │   ├── companyNewsPipeline.py
│   │   ├── industryNewsPipeline.py
│   │   ├── sectorNewsPipeline.py
│   │   ├── macroNewsPipeline.py
│   │   ├── USNewsPipeline.py
│   │   ├── worldNewsPipeline.py
│   │   ├── source_config.py
│   │   ├── urlFactories.py
│   │   ├── news_normalization.py
│   │   ├── scoring.py
│   │   ├── engine/
│   │   │   ├── article_scraper.py
│   │   │   ├── article_extraction.py
│   │   │   ├── listing_page_helper.py
│   │   │   ├── Normalization.py
│   │   │   └── playwright_runner.py
│   │   ├── extractors/
│   │   │   ├── barrons.py
│   │   │   ├── cnbc.py
│   │   │   ├── fool.py
│   │   │   ├── investing.py
│   │   │   ├── marketwatch.py
│   │   │   ├── morningstar.py
│   │   │   └── yahoo.py
│   │   ├── processing/
│   │   │   ├── news_normalization.py
│   │   │   └── scoring.py
│   │   ├── sources/
│   │   │   ├── source_config.py
│   │   │   └── urlFactories.py
│   │   ├── pipelines/
│   │   │   ├── companyNewsPipeline.py
│   │   │   ├── industryNewsPipeline.py
│   │   │   ├── sectorNewsPipeline.py
│   │   │   ├── macroNewsPipeline.py
│   │   │   ├── USNewsPipeline.py
│   │   │   ├── worldNewsPipeline.py
│   │   │   ├── job_builder.py
│   │   │   ├── _constants.py
│   │   │   ├── _entity_adapters.py
│   │   │   ├── _internal.py
│   │   │   ├── _orchestration.py
│   │   │   ├── _regionNewsPipeline.py
│   │   │   ├── _sector_rss.py
│   │   │   └── _shared.py
│   │   └── market_data/
│   │       ├── yfinance_client.py
│   │       ├── yFinanceData.py
│   │       ├── yFinanceNews.py
│   │       ├── yFinanceRealTime.py
│   │       └── urllist.py
│   └── agentCallers/
│       ├── README.md
│       ├── agentMain.py
│       ├── _paths.py
│       ├── _shared.py
│       ├── _macro_news_helpers.py
│       ├── _opportunist_payload_helpers.py
│       ├── _sector_opportunist_helpers.py
│       ├── _industry_opportunist_helpers.py
│       ├── _company_opportunist_helpers.py
│       ├── _strategist_helpers.py
│       ├── MacroNewsToSectors.py
│       ├── SectorOppurtunist.py
│       ├── IndustryOppuruntinst.py
│       ├── CompanyOppurtunist.py
│       ├── Strategist.py
│       ├── WorldNewsToSectors.py
│       ├── marketHealth.py
│       ├── researcher.py
│       ├── industryIntrest.py
│       ├── sectorIntrest.py
│       ├── agent_analysis/
│       │   ├── industry_interest.py
│       │   └── sector_interest.py
│       ├── agent_builders/
│       │   ├── company_opportunity.py
│       │   ├── opportunist_payload.py
│       │   └── strategist_payload.py
│       ├── agent_helpers/
│       │   ├── shared.py
│       │   ├── macro_news.py
│       │   ├── sector_opportunist.py
│       │   ├── industry_opportunist.py
│       │   ├── company_opportunist.py
│       │   ├── strategist.py
│       │   └── manager.py
│       ├── agent_stages/
│       │   ├── macro_news_to_sectors.py
│       │   ├── world_news_to_sectors.py
│       │   ├── sector_opportunist.py
│       │   ├── industry_opportunist.py
│       │   ├── company_opportunist.py
│       │   ├── strategist.py
│       │   └── manager.py
│       ├── agent_pipeline/
│       │   └── main.py
│       └── agent_runtime/
│           ├── init_agents.py
│           ├── market_health.py
│           └── researcher.py
├── web_dashboard/
│   ├── index.html
│   └── server.py
└── html_pages/
    ├── Yahoo/
    ├── barrons/
    ├── cnbc/
    ├── fool/
    ├── investing/
    ├── marketWatch/
    └── morningstar/
```


## 2. Major Subsystems

### Data Layer

- `Data/db.py`
  - Thin re-export layer for both `market_db.py` and `news_db.py`.
- `Data/db_common.py`
  - Shared SQLite connection and schema helpers.
- `Data/market_db.py`
  - Market-side persistence for sectors, industries, companies, rankings, and snapshots.
- `Data/news_db.py`
  - News-side persistence for articles, macro events, impact tables, strategist summaries, and processing markers.
- `Data/db_helpers/`
  - Stable import surface for the rest of the app.
  - `market.py` re-exports market DB operations.
  - `news.py` re-exports news DB operations.
  - `queries.py` exposes common lookup functions like `get_all_companies()`.

Mental model:

```text
scripts / pipelines / agents
        ↓
  db_helpers/*
        ↓
 market_db.py + news_db.py
        ↓
     SQLite + schema.sql
```


### Webscraping System

This is the news ingestion pipeline.

- `Python Scripts/Webscraping/pipelines/`
  - Planning and orchestration layer.
  - Builds crawl jobs and decides what gets fetched.
- `Python Scripts/Webscraping/engine/`
  - Crawl and extraction runtime.
  - Handles page crawling, link extraction, and rendered-page fallback.
- `Python Scripts/Webscraping/extractors/`
  - Source-specific parsing rules for sites like CNBC, Yahoo, Barrons, Fool, and MarketWatch.
- `Python Scripts/Webscraping/processing/`
  - Normalization, dedupe support, and evidence scoring.
- `Python Scripts/Webscraping/market_data/`
  - Yahoo Finance-based market and discovery data ingestion.

News pipeline flow:

```text
source_config/urlFactories
        ↓
pipelines/job_builder.py
        ↓
pipelines/companyNewsPipeline.py
pipelines/industryNewsPipeline.py
pipelines/sectorNewsPipeline.py
pipelines/macroNewsPipeline.py
pipelines/USNewsPipeline.py
pipelines/worldNewsPipeline.py
        ↓
engine/article_scraper.py
        ↓
extractors/* + engine/article_extraction.py
        ↓
processing/news_normalization.py + processing/scoring.py
        ↓
Data/news_db.py
```

Important note:

- The top-level files like `Webscraping/companyNewsPipeline.py` are mostly compatibility wrappers.
- The real implementation now lives in `Webscraping/pipelines/`.


### Agent Research System

This is the staged LLM research and decision-support pipeline.

- `agent_builders/`
  - Builds structured payloads for model stages.
- `agent_helpers/`
  - Loads DB context, parses model output, saves impacts, and gathers market/account data.
- `agent_stages/`
  - Model-facing stage code that builds prompts, calls Ollama, validates outputs, and returns normalized results.
- `agent_analysis/`
  - Ranking helpers for top sectors and industries.
- `agent_pipeline/main.py`
  - Higher-level orchestration for running multiple stages in sequence.
- Top-level files in `agentCallers/`
  - Mostly compatibility wrappers for older imports and direct script execution.

Current stage flow:

```text
macro/world news
        ↓
macro_news_to_sectors.py
world_news_to_sectors.py
        ↓
sector_opportunist.py
        ↓
industry_opportunist.py
        ↓
company_opportunist.py
        ↓
strategist.py
        ↓
manager.py
```

Supporting flow:

```text
agent_stages/*
        ↓
agent_helpers/*
        ↓
agent_builders/* + db_helpers/*
        ↓
Ollama + SQLite + Alpaca
```


### Legacy Trading Runtime

- `Python Scripts/main.py`
  - Older trading loop with prediction-based buy/sell planning.
- `Python Scripts/Trading.py`
  - Thin compatibility facade that re-exports the legacy trading helpers.
- `Python Scripts/trading_support/`
  - Focused trading modules split by concern: client setup, account diagnostics, stock orders, option contracts, and option position management.
- `Python Scripts/MarketData.py`
  - Quote, option, and streaming-related utilities.

This side of the repo looks older than the agent pipeline and is more prototype-like.


### Web Dashboard

- `web_dashboard/server.py`
  - Small Flask server that starts/stops local scripts.
- `web_dashboard/index.html`
  - UI for that server.

Dashboard control flow:

```text
browser
  ↓
web_dashboard/server.py
  ↓
subprocess.Popen(...)
  ↓
main.py or MarketData.py
```


## 3. Public Entry Points

If you want to understand the repo quickly, these are the main starting points:

- `README.md`
  - project direction and subsystem overview
- `Python Scripts/Webscraping/README.md`
  - scraping pipeline explanation
- `Python Scripts/agentCallers/README.md`
  - agent folder layout
- `Python Scripts/agentCallers/agent_pipeline/main.py`
  - best place to understand current multi-stage agent orchestration
- `Python Scripts/agentCallers/agent_stages/manager.py`
  - final decision-support stage
- `Data/db_helpers/__init__.py`
  - stable DB access surface


## 4. Dependency Map By Folder

### `Data/`

```text
db_helpers/__init__.py
  ├── common.py
  ├── market.py -> market_db.py
  ├── news.py -> news_db.py
  └── queries.py
```

### `Python Scripts/Webscraping/`

```text
top-level wrappers
  ↓
pipelines/*
  ↓
engine/*
  ↓
extractors/* + processing/*
  ↓
Data/news_db.py
```

### `Python Scripts/agentCallers/`

```text
compatibility wrappers
  ↓
agent_pipeline/*
  ↓
agent_stages/*
  ↓
agent_helpers/*
  ↓
agent_builders/* + agent_analysis/*
  ↓
db_helpers/* + Ollama + Alpaca
```


## 5. Suggested Reading Order

If you are onboarding to the current codebase, this order gives the clearest picture:

1. `README.md`
2. `Python Scripts/Webscraping/README.md`
3. `Data/db_helpers/__init__.py`
4. `Python Scripts/agentCallers/README.md`
5. `Python Scripts/agentCallers/agent_pipeline/main.py`
6. `Python Scripts/agentCallers/agent_stages/strategist.py`
7. `Python Scripts/agentCallers/agent_stages/manager.py`


## 6. Quick Interpretation

The repository currently has four layers living side by side:

1. persistent storage in `Data/`
2. deterministic ingestion in `Webscraping/`
3. agentic reasoning in `agentCallers/`
4. older execution/runtime scripts in `main.py`, `Trading.py`, and `MarketData.py`

The newer architecture is centered around:

```text
Data
  + Webscraping
  + agentCallers
```

while the older architecture is centered around:

```text
main.py
  + Trading.py
  + trading_support/*
  + MarketData.py
```

That split explains why the repo feels mixed: it contains both the older trading prototype and the newer research-first pipeline.
