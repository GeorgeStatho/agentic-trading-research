# Code Syntax Tree And Map

This file is a high-level map of the repository as it exists now.
Start with the [documentation index](README.md), which separates current documents
from archived snapshots.
It is meant to answer two questions quickly:

1. What is in this repo?
2. How do the main pieces connect?

For the supported extension boundaries and the feature checklist, use
[docs/ARCHITECTURE.md](ARCHITECTURE.md) and
[docs/FEATURE_PLAYBOOK.md](FEATURE_PLAYBOOK.md). This map intentionally
reflects the canonical packages; the old top-level compatibility wrappers were removed.


## 1. Syntax Tree

Research foundation additions are mapped in [Research Phase 0](RESEARCH_PHASE0.md):
`Data/research_foundation_contracts.py`, the `company_evidence`, `research_foundation`,
and `research_observations` database modules, and the `research_versions` and
`evidence_research_policy` services. These supply Phase 0 persistence and comparison
identity; discovery workers and provider adapters are not yet implemented.

```text
Stock-trading-experiment/
├── README.md
├── AgenticStockExperiemnt.md
├── requirements.txt
├── config/
│   ├── Modelfile
│   └── model_knowledge_cutoffs.csv
├── Data/
│   ├── market_db_modules/
│   ├── news_db_modules/
│   ├── db_common.py
│   ├── market_schema.sql
│   ├── news_schema.sql
│   ├── macro_events.json
│   └── logs/
├── Python Scripts/
│   ├── main.py
│   ├── option_manager_main.py
│   ├── news_collector_main.py
│   ├── marketdata_eod_sync_main.py
│   ├── db_backup_main.py
│   ├── services/
│   │   ├── core/         settings, paths, env parsing, status files
│   │   ├── runtime/      worker orchestration, startup checks, telemetry, db_backup
│   │   ├── trading/      broker gateway, executor, journal, follower accounts
│   │   ├── options/      DTE buckets, pricing, symbols, position state and exits
│   │   ├── gates/        deterministic entry gates and their policies
│   │   ├── thesis/       thesis capture, checks, validation, event reviews
│   │   ├── research/     shadow research loop
│   │   ├── discovery/    event discovery, SEC market events, routing, company evidence
│   │   └── evaluation/   backtests, benchmarks, forward evaluation, attribution
│   ├── trading_support/
│   │   ├── __init__.py
│   │   ├── account.py
│   │   ├── clients.py
│   │   ├── option_contracts.py
│   │   ├── option_positions.py
│   │   └── stock_orders.py
│   ├── Companies/
│   │   └── Company.py
│   ├── WritingToDataBase/
│   │   └── stringtoJson.py
│   ├── Webscraping/
│   │   ├── README.md
│   │   ├── APIs/
│   │   │   ├── alpacaNewsAPI.py
│   │   │   ├── foolAPI.py
│   │   │   ├── gurufocusAPI.py
│   │   │   ├── InvestingAPI.py
│   │   │   └── morningStarAPI.py
│   │   ├── company_evidence/
│   │   │   ├── sec.py
│   │   │   ├── sec_current.py
│   │   │   ├── sec_facts.py
│   │   │   ├── issuer.py
│   │   │   ├── releases.py
│   │   │   └── yahoo_consensus.py
│   │   ├── engine/
│   │   │   ├── article_scraper.py
│   │   │   ├── article_extraction.py
│   │   │   ├── page_fetch.py
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
│   │   │   └── _sector_rss.py
│   │   └── market_data/
│   │       ├── hydration.py
│   │       ├── yfinance_client.py
│   │       ├── yFinanceData.py
│   │       ├── yFinanceNews.py
│   │       ├── yFinanceRealTime.py
│   │       └── urllist.py
│   └── agentCallers/
│       ├── README.md
│       ├── agent_analysis/
│       │   ├── industry_interest.py
│       │   └── sector_interest.py
│       ├── agent_builders/
│       │   ├── company_opportunity.py
│       │   ├── opportunist_payload.py
│       │   └── strategist_payload.py
│       ├── agent_helpers/
│       │   ├── llm_client.py      entry point: get_model_client, ask_llm_model, ask_classifier_model
│       │   ├── llm_vertex.py      Vertex/Gemini clients, model aliases, prompt cache
│       │   ├── llm_openai.py      OpenAI, OpenAI-compatible and llama.cpp
│       │   ├── llm_errors.py      provider exceptions and retry classification
│       │   ├── llm_usage.py       response diagnostics and llm_call_usage records
│       │   ├── text_utils.py
│       │   ├── macro_news.py
│       │   ├── sector_opportunist.py
│       │   ├── industry_opportunist.py
│       │   ├── company_opportunist.py
│       │   ├── strategist_evidence.py
│       │   └── manager_inputs.py
│       ├── agent_stages/
│       │   ├── macro_news_to_sectors.py
│       │   ├── sector_opportunist.py
│       │   ├── industry_opportunist.py
│       │   ├── company_opportunist.py
│       │   ├── strategist_runner.py
│       │   └── manager_runner.py
│       ├── agent_pipeline/
│       │   └── main.py
│       └── agent_runtime/
│           ├── init_agents.py
│           ├── market_health.py
│           └── researcher.py
├── web_dashboard/        Vite + React dashboard, served by nginx
│   ├── src/
│   │   ├── routes.tsx    pages and views
│   │   └── ui/           shared Tailwind components and charts
│   ├── nginx.conf
│   └── Dockerfile
├── api.py, api_routes/, api_support/   Flask API
├── scripts/              developer CLIs (project.py, research.py, compact_db.py, account_report.py, ...)
├── config/litestream.yml
├── docker-compose*.yml   main stack plus backup, observability, tunnel and bootstrap overlays
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

- `Data/db_common.py`
  - Shared SQLite connection and schema helpers, plus the swappable current database (`current_database()`, `use_database(path)`, `forget_setup()`).
- `Data/market_db_modules/`
  - Market-side persistence for sectors, industries, companies, rankings, and snapshots. `queries.py` holds `get_all_sectors/industries/companies`.
- `Data/news_db_modules/`
  - News-side persistence for articles, macro events, impact tables, strategist summaries, and processing markers.
- `Python Scripts/Webscraping/market_data/hydration.py`
  - yfinance `ensure_*_market_data` hydration of the market database.
- `tests/db_fixtures.py`
  - `temporary_database(test)`: gives a test its own current database.

Mental model:

```text
scripts / pipelines / agents
        ↓
market_db_modules/* + news_db_modules/*
        ↓
     db_common.py
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
- `Python Scripts/Webscraping/APIs/`
  - Per-site article discovery and content APIs (Fool, Morningstar, GuruFocus, Investing, and Alpaca News for Benzinga).
- `Python Scripts/Webscraping/company_evidence/`
  - Primary-evidence adapters: SEC filings and facts, configured issuer releases, and the Yahoo consensus/earnings-date adapter (see `docs/COMPANY_EVIDENCE_PROVIDERS.md`).
- `scripts/news_quality_report.py`, `scripts/check_extractors.py`
  - A read-only report on how much company-relevant news the strategist received, and a live per-domain extractor check (see `scripts/README.md`).

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
Data/news_db_modules/*
```

Important note:

- The implementation lives in `Webscraping/pipelines/`; the old top-level wrappers were removed.


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
  - Entrypoints only: `main.py` (strategist/manager runner), the offline evaluator and quote/backtest CLIs.

Current stage flow:

```text
macro/world news
        ↓
macro_news_to_sectors.py (macro and world news)
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
agent_builders/* + market_db_modules/* + news_db_modules/*
        ↓
Ollama + SQLite + Alpaca
```


### Legacy Trading Runtime

- `Python Scripts/main.py`
  - Older trading loop with prediction-based buy/sell planning.
- `Python Scripts/trading_support/`
  - Focused trading modules split by concern: client setup, account diagnostics, stock orders, option contracts, and option position management.

This side of the repo looks older than the agent pipeline and is more prototype-like.


### Web Dashboard

- `web_dashboard/`
  - Vite + React + TypeScript app, styled with Tailwind v4 and daisyUI 5. It has four pages: Live, Decisions, Research and System. `src/routes.tsx` lists them. [web_dashboard/README.md](../web_dashboard/README.md) maps the files.
- `api.py`, `api_routes/`, `api_support/`
  - The Flask API the dashboard reads. It never places trades or starts and stops the worker; its only writes are backtest and Exposure Map runs.
  - Dashboard payloads in `api_support/`: `dashboard.py` (KPIs, risk controls, rankings),
    `open_positions.py`, `company_decisions.py` (with the market regime), `decision_expectancy.py`
    and `bot_status.py` (loop health, also read by `/api/health/components`).
  - `api_routes/reports.py` serves reports the CLI tools already compute: execution quality, exit and
    decision attribution, the macro calendar and LLM spend. Their payloads are in `api_support/reports.py`.
  - `?format=csv` on `/api/executed-trades`, `/api/decision-autopsies` and `/api/decision-expectancy` returns
    the table as CSV through `request_utils.csv_response`.

Dashboard control flow:

```text
browser
  ↓
nginx (web container) ── /api/* ──> Flask API (api container)
                                         ↓
                              SQLite database + /shared status files
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
- `Python Scripts/agentCallers/agent_stages/manager_runner.py`
  - final decision-support stage
- `Data/db_common.py`
  - current database and connection handling


## 4. Dependency Map By Folder

### `Data/`

```text
market_db_modules/*, news_db_modules/*
  └── db_common.py
```

### `Python Scripts/Webscraping/`

```text
pipelines/*
  ↓
engine/*
  ↓
extractors/* + processing/*
  ↓
Data/news_db_modules/*
```

### `Python Scripts/agentCallers/`

```text
main.py (entrypoint)
  ↓
agent_pipeline/*
  ↓
agent_stages/*
  ↓
agent_helpers/*
  ↓
agent_builders/* + agent_analysis/*
  ↓
market_db_modules/* + news_db_modules/* + Ollama + Alpaca
```


## 5. Suggested Reading Order

If you are onboarding to the current codebase, this order gives the clearest picture:

1. `README.md`
2. `Python Scripts/Webscraping/README.md`
3. `Data/db_common.py`
4. `Python Scripts/agentCallers/README.md`
5. `Python Scripts/agentCallers/agent_pipeline/main.py`
6. `Python Scripts/agentCallers/agent_stages/strategist_runner.py`
7. `Python Scripts/agentCallers/agent_stages/manager_runner.py`


## 6. Quick Interpretation

The repository currently has four layers living side by side:

1. persistent storage in `Data/`
2. deterministic ingestion in `Webscraping/`
3. agentic reasoning in `agentCallers/`
4. older execution/runtime scripts in `main.py` and `trading_support/`

The newer architecture is centered around:

```text
Data
  + Webscraping
  + agentCallers
```

while the older architecture is centered around:

```text
main.py
  + trading_support/*
```

That split explains why the repo feels mixed: it contains both the older trading prototype and the newer research-first pipeline.


## V2 Part 1 measurement foundations

- `Python Scripts/services/trading/outcome_reconciliation.py`: broker-fill round trips,
  invoked at worker startup and after position-management cycles.
- `Data/news_db_modules/provenance.py`: article/catalyst/root grouping and
  transactional source-count updates at ingestion.
- `Python Scripts/services/gates/regime_history.py`: dated SPY/VIX history persisted in
  `historical_underlying_bars` and consumed by the regime classifier.
- `Data/market_db_modules/shadow_gates.py`: immutable decision-time gate verdicts
  and their joins to realized outcomes.
- `scripts/v2_part1.py`: outcome, provenance, regime, and gate maintenance commands.
- [TRADINGBOT_V2_PART1_CHANGES.md](v2/TRADINGBOT_V2_PART1_CHANGES.md): implementation
  rationale, validation results, and outstanding deployment checks.

## V2 Part 2 telemetry foundations

- `Python Scripts/services/core/pipeline_version.py`: effective configuration, model,
  prompt and source manifests, hashed for historical identification.
- `Python Scripts/services/runtime/candidate_lifecycle.py` and
  `Data/market_db_modules/candidate_lifecycle.py`: candidate IDs, immutable
  checkpoints, canonical rejection storage, cohort funnels and hash-checked reads
  (`stage_payloads`; every snapshot reader checks rows with `foundation_store.verified_envelope`).
- `Python Scripts/services/runtime/execution_telemetry.py` and
  `Data/market_db_modules/execution_telemetry.py`: quote capture, broker-fill
  reconciliation, coverage, fill rates and slippage reports.
- `Python Scripts/services/runtime/position_telemetry.py` and
  `Data/market_db_modules/position_outcomes.py`: entry-order lifetimes, sampled
  extrema, final-exit attribution and daily post-exit/benchmark observations.
- `Data/market_db_modules/telemetry_schema.py`: additive legacy schema migration.
- `scripts/v2_part2.py`: execution, funnel, lifecycle, exits, fills and post-exit CLI.
- `tests/test_v2_part2.py`: telemetry invariants and handoff coverage.
- [TRADINGBOT_V2_PART2_CHANGES.md](v2/TRADINGBOT_V2_PART2_CHANGES.md): choices,
  measurement definitions, commands, validation and deployment acceptance.

## V2 Part 3 provenance and catalysts

- `Data/catalyst_models.py`: capture taxonomy, coarse modelling families (including `corporate_action` and
  the rule-detected `commentary` family), `CatalystState` and explicit calendar-time decay priors.
- `Data/catalyst_schema.py` / `Data/catalyst_schema.sql`: shared additive migration
  for catalysts, roots, candidate associations, gate observations and record links.
- `Data/news_db_modules/catalyst_labels.py`: insert-only model labels for rule failures, latest-label
  (point-in-time) and pending queries, status and review reports.
- `Python Scripts/agentCallers/agent_stages/catalyst_classifier.py` / `agent_helpers/catalyst_classifier.py`:
  the cheap-model labelling stage, its prompt, schema, inputs and answer validation.
- `Python Scripts/services/discovery/catalyst_classification.py`: `classify_pending_catalysts`, run by the news
  collector before response registration when `CATALYST_CLASSIFIER_ENABLED`.
- `scripts/catalysts.py`: `classify`, `status` and `review` for the backfill.
- `Data/news_db_modules/catalysts.py`: state refresh, frozen candidate associations,
  impact-root validation, backfill and family coverage report.
- `Data/news_db_modules/provenance.py`: source-chain grouping and original root
  timing; classifications come from `config/source.json`.
- `Python Scripts/services/gates/catalyst_gates.py`: provenance/primary-source exception,
  freshness decay, shadow recording and enforced admission checks.
- `Data/market_db_modules/catalyst_research.py`: immutable gate observations and
  executed-outcome coverage by catalyst-age quartile.
- `scripts/v2_part3.py`: catalyst backfill, family and freshness reports.
- `tests/test_v2_part3.py`: Part 3 invariants and integration tests.
- [TRADINGBOT_V2_PART3_CHANGES.md](v2/TRADINGBOT_V2_PART3_CHANGES.md): implementation
  decisions, clock semantics, configuration, validation and acceptance limits.

## V2 Part 4 pricing gates

- `Python Scripts/services/research/price_response.py`: completed-bar alignment, pre-event
  joint betas, residual returns, sampled MFE/MAE and shadow absorption verdicts.
- `Python Scripts/services/gates/pricing_policy.py`: versioned sample, horizon, quote
  and collection defaults; pricing gates have no enforcement mode.
- `Data/market_db_modules/pricing_schema.sql` / `pricing_research.py`: additive
  response/event storage, immutable observations, as-of statistics and coverage.
- `Python Scripts/services/discovery/catalyst_response_collector.py`: bounded history refresh,
  independent catalyst outcomes and forward returns from frozen candidate prices.
  The news collector invokes it after ingestion; CLI also supports cached-only runs.
- `Python Scripts/services/gates/option_hurdle_gate.py`: ATM call/put pairing, quote checks,
  expiry-to-holding-horizon scaling and expected/implied-move comparison.
- `Python Scripts/services/gates/pricing_gates.py`: candidate creation and pre-selection
  adapters; hashed snapshots and canonical shadow rejections.
- `Python Scripts/agentCallers/agent_helpers/market_context/option_chain.py`:
  retains ATM research quotes separately from the existing OTM selector list.
- `Python Scripts/services/gates/market_regime_gate.py`: scope opposition, hard data/macro
  veto semantics and legacy-versus-weighted verdicts.
- `Python Scripts/services/gates/market_regime_variants.py`: shadow-only `REGIME_SPY_TREND` variant
  (blocks calls only when SPY's 5- and 20-day returns are both down, puts on the mirror); always
  allowed, recorded beside `REGIME` under its own stage and `market_regime_spy_trend_gate` key.
- `Python Scripts/services/gates/entry_extension_gate.py`: `ENTRY_EXTENSION` shadow gate; allows when
  the prior 5-day move in the proposed direction is ≤ 0 or the manager's `overextension_risk` is low.
  `prior_five_day_move` is the one definition of that move (the gate ledger reads its stored copy); the
  pick score scores the gate's recorded verdict.
- `Python Scripts/services/gates/tradability_gate.py`: `TRADABILITY` shadow gate, judged before the
  strategist: fetches the default bucket's chain with the manager's Alpaca helpers, takes the selector's
  top pick per side and prices it as the executor does against the cycle's premium cap (buying power read
  once per cycle). Failures are unknown verdicts; it never skips the strategist or manager.
- The executor logs `regime_sized_order_qty` (the regime gate's `sizing_multiplier` applied to the
  real quantity) on each candidate and execution; orders still use the real quantity.
- `scripts/v2_part4.py`: collection budgets, family statistics and absorption reports.
- `tests/test_v2_part4.py`: numerical, timing, quote, migration and integration tests.
- [TRADINGBOT_V2_PART4_CHANGES.md](v2/TRADINGBOT_V2_PART4_CHANGES.md): choices,
  configuration, validation and operational acceptance limits.

## V2 Part 5 contracts and execution

- `Python Scripts/services/gates/contract_policy.py`: versioned rollout modes and
  thresholds for liquidity, scenario selection, risk clusters and exit rungs.
- `Python Scripts/services/gates/liquidity_gate.py`: size/age/spread verdicts, selector
  filtering, canonical reasons and execution-quote validation.
- `Python Scripts/services/options/reference_price.py`: the per-share price a contract is sized and capped at
  (ask, mid, bid, then last trade/close); shared by the trade executor and the TRADABILITY gate.
- `Python Scripts/services/core/trade_sides.py`: `trade_side` (+1 call, -1 put) for the shadow gates, pick score
  and follower stop; listed in those gates' proof files.
- `Python Scripts/services/options/scenario_selector.py`: empirical scenarios repriced with Black-Scholes
  (`services/options/option_pricing.py`),
  independent cost components, P&L quantiles and strict-hybrid-pool ranking.
- `Python Scripts/services/trading/limit_entry.py`: offline bounded entry state machine,
  partial-fill/cancellation rules, price caps and invalidation; no live broker loop.
- `Python Scripts/services/gates/risk_clusters.py` and `config/risk_clusters.csv`:
  maintained assignments and projected position/notional caps.
- `Data/market_db_modules/contract_research.py`: additive company cluster migration,
  immutable observations and market-fill baseline/coverage reports.
- `Python Scripts/services/gates/contract_observations.py`: candidate checkpoint and
  research observation adapters for Part 5 comparisons.
- `Python Scripts/services/options/position_exit_policy.py`: dated entry/IV inputs,
  catalyst time expiry, volatility collapse and shadow priority comparisons.
- `Python Scripts/services/thesis/thesis_capture.py`: classified catalyst holding horizons
  with a labelled legacy DTE fallback; the thesis writer retains fractional days.
- `Python Scripts/trading_support/_option_positions_strategy.py`: new exit rungs
  in the existing risk/expiration/trailing ladder and coactive attribution.
- `scripts/v2_part5.py`: baseline and observation/mapping coverage reports.
- `tests/test_v2_part5.py`: numerical, state-machine, migration and runtime tests.

## V2 Part 6 research infrastructure

- `services/research/research_contract.py`: common cutoff assertions, research policy and seven
  deterministic strategy decisions. Paths here and below are under `Python Scripts/`. `shares_drift`
  is simulated as stock, not options (`stock_position` in `Data/market_db_modules/research_runs.py`), and
  is listed in `UNPAIRED_STRATEGIES`, so it never pairs with or competes against the option strategies.
- `services/research/research_inputs.py`: verified T2 snapshot adapters, mechanical features,
  gate observations and frozen contract selection. The selector's scoped clock in
  `agentCallers/agent_helpers/deterministic_option_selector/market.py` keeps historical
  DTE calculations independent of the current date.
- `services/research/shadow_portfolio.py`: ask-entry/bid-exit daily counterfactuals, fees,
  coactive exit reasons, gate matrices and three expectancy denominators.
- `Data/market_db_modules/research_schema.sql`, `research_runs.py`: additive run,
  frozen-input, shadow-position, report, promotion and champion storage; exact-N
  enrollment, quote availability checks and same-capital SMH/QQQ comparisons.
- `Data/market_db_modules/research_reports.py`: monthly/full-cohort rankings,
  confidence calibration, cited reports and evidence-qualified promotions; `pooled_gate_report`
  pools one gate across runs whose rows carry its current code and policy hash (move-scored, or
  `PRECISION_SCORED_BLOCKS` such as TRADABILITY via `hard_block_outcome`).
- `services/research/research_promotion.py`: policy/source-bound gate approvals and the
  singleton champion's capital guard. Runtime policy factories use this guard.
- `services/discovery/catalyst_response_collector.py`: refreshes eligible research runs after
  the existing catalyst/forward-return collection. `agentCallers/decision_pipeline.py` records
  candidate completion before research enrollment.
- `agentCallers/agent_stages/manager_prompt.py`, `manager_normalization.py`,
  `manager_model.py`: versioned ordinal thesis, timing and execution confidence.
- `services/research/pick_score.py`: the log-only, versioned pick score (the ENTRY_EXTENSION rule,
  freshness, relative strength). `decision_pipeline.py` attaches it after the manager; it and the three confidence
  components are columns on `manager_decision_history`. Nothing orders or blocks on it yet.
- `scripts/v2_part6.py`: start, collect, report, pooled `report-gate`, status and explicit promotion commands.
- `tests/test_v2_part6.py`: cutoff, quote-path, denominator, calibration, freeze,
  coverage and promotion tests. See [TRADINGBOT_V2_PART6_CHANGES.md](v2/TRADINGBOT_V2_PART6_CHANGES.md) for assumptions
  and the distinction between implementation and live evidence acceptance.
- [TRADINGBOT_V2_PART5_CHANGES.md](v2/TRADINGBOT_V2_PART5_CHANGES.md): implementation
  rationale, configuration, validation and explicit E1/live-thesis deferrals.

Company earnings/guidance ingestion, immutable evidence packages, and offline replay
are described in [Research Phase 1](RESEARCH_PHASE1.md). Provider coverage and
limits are listed in [the capability matrix](COMPANY_EVIDENCE_PROVIDERS.md).

The rotating company-event scan, durable queue, and shadow research intake are
documented in [Research Phase 2](RESEARCH_PHASE2.md). Discovery is disabled by
default and does not forward candidates to broker execution.

Phase 3 adds bounded shadow investigation with typed archive retrieval, durable
sessions, daily allowances and offline replay. It is disabled by default. See
[Research Phase 3](RESEARCH_PHASE3.md) and `scripts/research.py`. `services/research/research_search.py`
holds the flag-gated `search_archive` hit queries over the `news_article_fts`/`evidence_document_fts`
indexes and freezes article hits as immutable `news_archive` documents.

Cross-cutting infrastructure: `pyproject.toml` declares the import roots (`pip install -e .`);
`services/core/settings.py` is the one typed settings object, loaded at process start;
`services/gates/entry_gates.py` is the entry-gate registry; `agentCallers/agent_contracts.py` holds the
typed pipeline objects. See [DEVELOPMENT.md](DEVELOPMENT.md).

Phase 4 adds a versioned `evidence_thesis` to the strategist output, deterministic claim checks
(`services/thesis/evidence_thesis*.py`), the `EVIDENCE_THESIS` entry gate, manager handoff of the same
checked thesis, and shadow theses for finished research sessions (`services/research/research_thesis.py`).
Checks persist in `evidence_thesis_checks`. See [Research Phase 4](RESEARCH_PHASE4.md).

Phase 5 treats the manager's call/put and DTE bucket as a proposal. `services/gates/trade_economics.py` reprices the
side-matching contracts with Black-Scholes (`services/options/option_pricing.py`) on dated empirical catalyst scenarios
and stores an immutable report in `candidate_economics_reports`. It records the promotion-guarded
`TRADE_ECONOMICS` entry gate before the streaming callback, and rechecks the report at order submission.
Policy: `services/gates/trade_economics_policy.py`. See [Research Phase 5](RESEARCH_PHASE5.md).

Phase 6 reviews open positions when a material event arrives. `services/thesis/thesis_event_review.py` claims settled
discovery `position_review` items (`claim_position_reviews`) inside the option manager's cycle, and reuses the daily
validator's evidence pack, adjudicator and arbiter. It records shadow-only verdicts, would-be actions and what changed
in `thesis_event_reviews`. Policy: `services/thesis/thesis_event_review_policy.py`. See [Research Phase 6](RESEARCH_PHASE6.md).

Phase 7 makes forward evidence accumulate and ties promotion to criteria written beforehand. `begin_candidate` opens a
rolling production cohort (`services/evaluation/forward_cohorts.py`); while a challenger model pair is on, the live route freezes a
decision observation with every DTE bucket's chain (`services/evaluation/decision_observations.py`). The collector finalizes the
incumbent's and the challenger's outputs on it (`services/evaluation/challenger_evaluation.py`, `paired_decisions.py`), and the option
manager polls Alpaca closing marks (`services/research/research_marks.py`). Paired reports, frozen acceptance criteria and approvals live
in `Data/market_db_modules/forward_comparison.py`; route ledgers in `discovery_evaluation.py` and `research_outcomes.py`.
See [Research Phase 7](RESEARCH_PHASE7.md).

LLM replay backtests (`scripts/project.py llm-backtest`, `agentCallers/llm_backtest.py`) re-ask a manager model, such as a
local llama.cpp model, on every stored decision's frozen input, then run production's continuation, selection, regime and
thesis steps as of the decision time (`services/evaluation/llm_backtest.py`). Every frozen input a replay needs is read through
`FrozenDecision` (`Data/market_db_modules/llm_replay_store.py`); a decision whose snapshots fail their hash check is
reported as `snapshot_hash_mismatch` and the run continues. Directional answers select from the DTE-bucket chain that
production saved in `candidate_snapshots`; answers in another bucket, and decisions before those snapshots, are reported as
`bucket_chain_unavailable`. Trades are recorded as ordinary backtest runs (`run_decision_backtest`): stored quotes where they
exist, otherwise Black-Scholes marks on daily closes. Answers are cached in `llm_replay_cache`; `--model stored` replays
production's own answers as the like-for-like baseline. `--decision-ids-file` replays only the listed decisions. Unless the model is
`stored`, decisions within a model's knowledge cutoff plus 30 days (`config/model_knowledge_cutoffs.csv`,
`services/evaluation/model_cutoffs.py`) are dropped and counted as `excluded_pre_knowledge_cutoff`, and a model missing from the table is refused
unless `--allow-unknown-cutoff`. Eligibility, the premium cap and portfolio limits come
from each candidate's recorded pipeline manifest; directional answers whose recorded selector settings differ from
the local ones are reported as `selector_config_mismatch`. `--fetch-missing-bars` downloads daily history for
traded symbols that have none. `--min-hours-between N` replays a company's decision only when it is N hours
after the last one replayed for it, chosen by time alone; compare it with a `stored` run using the same N. `--replay-strategist` has the model answer
each decision's frozen strategist input too (the candidate's `pre_strategist` checkpoint) and hands that answer to
the manager exactly as production does; strategist answers are cached separately. `--score-direction`
(`services/evaluation/llm_direction_score.py`) grades the same replayed answers by the stock's return over the next 5 and 10
trading-day closes instead of trading options, so decisions from before the bucket-chain snapshots (frozen inputs
start 2026-07-21) can be compared too; it reports how every scored stock moved as the bar a model must beat.

### Benchmark sets

`Data/market_db_modules/benchmarks.py` stores insert-only `benchmark_sets`, `benchmark_items` (dev/holdout split and
stratum per frozen decision) and `benchmark_runs` (holdout once per set, model, prompts and strategist flag).
`services/evaluation/benchmark_selection.py` builds a stratified, seeded set; `services/evaluation/benchmark_runner.py` replays a split
through `run_llm_backtest`, scores each item with `decision_attribution` and compares two runs item by item.
`scripts/research.py benchmark` is the CLI. It screens challengers and never promotes. Plan:
[PLAN_B_PIT_REPLAY_BENCHMARK](finharness/PLAN_B_PIT_REPLAY_BENCHMARK.md).

### Decision attribution

`services/evaluation/decision_attribution.py` splits a manager decision's outcome into evidence quality, direction and
expression (pure functions, plus `bootstrap_se`). `Data/market_db_modules/decision_attribution.py` stores one
`decision_attributions` row per decision, recomputed while unsettled or on a version change.
`services/evaluation/decision_attribution_runner.py` fills it from daily closes in each collector cycle.
`Data/market_db_modules/decision_attribution_report.py` backs `scripts/research.py attribution`,
`Data/market_db_modules/gate_ledger.py` backs `scripts/research.py gate-ledger` (every gate stage and two tags scored on the 5-day residual move),
`Data/market_db_modules/drift_report.py` backs `scripts/research.py drift-report` (the 5-day residual from each catalyst's first
observation, by family, point-in-time direction and age since first seen), and
`api_support/decision_autopsy.py` shows each decision's labels.

## Follower accounts

`services/trading/trading_accounts.py` loads `TRADING_ACCOUNTS` from the environment. Production is the read-only
primary, and each named follower has its own paper keys and strategy (`debit_spread`, `itm_single`, `longer_dte` or
`shares`). `services/trading/account_strategies.py` turns one production entry into a follower's order plan or a `Skip`.
It's pure: no broker or DB access. `services/trading/follower_fleet.py`, built in `Python Scripts/main.py`, places those
orders and closes each follower position when production's position is gone. Partial production exits are not mirrored.
Optional per-account settings: `ACCOUNT_<NAME>_TAKE_BLOCKED=true` (`debit_spread`/`shares` only) also takes picks production
skipped only for the premium cap (`FollowerFleet.mirror_blocked`, rows tagged `entry_kind='premium_cap_blocked'`, kept out of
`account_summary`/`follower_report` unless `entry_kind=` asks), and `ACCOUNT_<NAME>_EXIT_POLICY=stock_stop|partial_profit`
(`FOLLOWER_STOCK_STOP_PCT`, `FOLLOWER_PARTIAL_PROFIT_PCT`, `FOLLOWER_PARTIAL_PROFIT_FRACTION`). The `FOLLOWER_*` knobs are
`get_settings().followers` (`services/core/settings.py`); a bad value leaves it `None` and the fleet off. Followers may share one Alpaca
account's keys, so a variant needs a new follower name, not new credentials.
Positions are stored by `Data/market_db_modules/account_positions.py` (schema in `account_positions_schema.sql`).
`account_summary` there backs `scripts/account_report.py`, and `follower_report` backs `/api/follower-accounts`
(Research › Followers).

## Discovery routing and market events

`services/discovery/market_events.py` reads EDGAR's market-wide current-filings feed
(`Webscraping/company_evidence/sec_current.py`) in each news-collector cycle. It gives companies outside the ranking
discovery interest, and nothing more (`DISCOVERY_MARKET_EVENT_LIMIT`, default 0 = off).
`services/discovery/discovery_routing.py` picks the admitted entries that `DISCOVERY_MODE=enforce` hands to the
production strategist and manager (`agentCallers/decision_pipeline.py`). Each catalyst is routed once, only while fresh,
and only after the collector has refreshed that company's news. See [Research Guide](RESEARCH_GUIDE.md) and
[Research Phase 7](RESEARCH_PHASE7.md).

## Storage, backups and loop health

- `Data/market_db_modules/compact_storage.py`: content-addressed `json_blobs` for `candidate_snapshots`, and compressed
  provider bodies. New rows are written this way. `scripts/compact_db.py` converts older rows
  ([scripts/README.md](../scripts/README.md#database-compaction)).
- `Python Scripts/db_backup_main.py` + `services/runtime/db_backup.py`: the nightly full copy, plus a Litestream replica-lag
  check (`config/litestream.yml`, `docker-compose.backup.yml`). See [BACKUPS.md](BACKUPS.md).
- `services/core/status_files.py`: each loop's heartbeat file (`/shared/*_status.json`). `api_routes/health.py` turns
  them into `/api/health/components`, which returns 503 when any loop is down, stalled or failed recently.
