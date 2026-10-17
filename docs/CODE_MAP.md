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
│   └── Modelfile
├── Data/nd evidence checks
Strategist thesis
Trade economics
Manager a
│   ├── db_common.py
│   ├── market_schema.sql
│   ├── news_schema.sql
│   ├── macro_events.json
│   └── logs/
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
│   │   ├── engine/
│   │   │   ├── article_scraper.py
│   │   │   ├── article_extraction.py
│   │   │   ├── listing_page_helper.py
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
│       │   ├── shared.py
│       │   ├── macro_news.py
│       │   ├── sector_opportunist.py
│       │   ├── industry_opportunist.py
│       │   ├── company_opportunist.py
│       │   ├── strategist.py
│       │   └── manager.py
│       ├── agent_stages/
│       │   ├── macro_news_to_sectors.py
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


## V2 Part 1 measurement foundations

- `Python Scripts/services/outcome_reconciliation.py`: broker-fill round trips,
  invoked at worker startup and after position-management cycles.
- `Data/news_db_modules/provenance.py`: article/catalyst/root grouping and
  transactional source-count updates at ingestion.
- `Python Scripts/services/regime_history.py`: dated SPY/VIX history persisted in
  `historical_underlying_bars` and consumed by the regime classifier.
- `Data/market_db_modules/shadow_gates.py`: immutable decision-time gate verdicts
  and their joins to realized outcomes.
- `scripts/v2_part1.py`: outcome, provenance, regime, and gate maintenance commands.
- [TRADINGBOT_V2_PART1_CHANGES.md](v2/TRADINGBOT_V2_PART1_CHANGES.md): implementation
  rationale, validation results, and outstanding deployment checks.

## V2 Part 2 telemetry foundations

- `Python Scripts/services/pipeline_version.py`: effective configuration, model,
  prompt and source manifests, hashed for historical identification.
- `Python Scripts/services/candidate_lifecycle.py` and
  `Data/market_db_modules/candidate_lifecycle.py`: candidate IDs, immutable
  checkpoints, canonical rejection storage, cohort funnels and hash-checked reads.
- `Python Scripts/services/execution_telemetry.py` and
  `Data/market_db_modules/execution_telemetry.py`: quote capture, broker-fill
  reconciliation, coverage, fill rates and slippage reports.
- `Python Scripts/services/position_telemetry.py` and
  `Data/market_db_modules/position_outcomes.py`: entry-order lifetimes, sampled
  extrema, final-exit attribution and daily post-exit/benchmark observations.
- `Data/market_db_modules/telemetry_schema.py`: additive legacy schema migration.
- `scripts/v2_part2.py`: execution, funnel, lifecycle, exits, fills and post-exit CLI.
- `tests/test_v2_part2.py`: telemetry invariants and handoff coverage.
- [TRADINGBOT_V2_PART2_CHANGES.md](v2/TRADINGBOT_V2_PART2_CHANGES.md): choices,
  measurement definitions, commands, validation and deployment acceptance.

## V2 Part 3 provenance and catalysts

- `Data/catalyst_models.py`: capture taxonomy, coarse modelling families,
  `CatalystState` and explicit calendar-time decay priors.
- `Data/catalyst_schema.py` / `Data/catalyst_schema.sql`: shared additive migration
  for catalysts, roots, candidate associations, gate observations and record links.
- `Data/news_db_modules/catalysts.py`: state refresh, frozen candidate associations,
  impact-root validation, backfill and family coverage report.
- `Data/news_db_modules/provenance.py`: source-chain grouping and original root
  timing; classifications come from `config/source.json`.
- `Python Scripts/services/catalyst_gates.py`: provenance/primary-source exception,
  freshness decay, shadow recording and enforced admission checks.
- `Data/market_db_modules/catalyst_research.py`: immutable gate observations and
  executed-outcome coverage by catalyst-age quartile.
- `scripts/v2_part3.py`: catalyst backfill, family and freshness reports.
- `tests/test_v2_part3.py`: Part 3 invariants and integration tests.
- [TRADINGBOT_V2_PART3_CHANGES.md](v2/TRADINGBOT_V2_PART3_CHANGES.md): implementation
  decisions, clock semantics, configuration, validation and acceptance limits.

## V2 Part 4 pricing gates

- `Python Scripts/services/price_response.py`: completed-bar alignment, pre-event
  joint betas, residual returns, sampled MFE/MAE and shadow absorption verdicts.
- `Python Scripts/services/pricing_policy.py`: versioned sample, horizon, quote
  and collection defaults; pricing gates have no enforcement mode.
- `Data/market_db_modules/pricing_schema.sql` / `pricing_research.py`: additive
  response/event storage, immutable observations, as-of statistics and coverage.
- `Python Scripts/services/catalyst_response_collector.py`: bounded history refresh,
  independent catalyst outcomes and forward returns from frozen candidate prices.
  The news collector invokes it after ingestion; CLI also supports cached-only runs.
- `Python Scripts/services/option_hurdle_gate.py`: ATM call/put pairing, quote checks,
  expiry-to-holding-horizon scaling and expected/implied-move comparison.
- `Python Scripts/services/pricing_gates.py`: candidate creation and pre-selection
  adapters; hashed snapshots and canonical shadow rejections.
- `Python Scripts/agentCallers/agent_helpers/market_context/option_chain.py`:
  retains ATM research quotes separately from the existing OTM selector list.
- `Python Scripts/services/market_regime_gate.py`: scope opposition, hard data/macro
  veto semantics and legacy-versus-weighted verdicts.
- `scripts/v2_part4.py`: collection budgets, family statistics and absorption reports.
- `tests/test_v2_part4.py`: numerical, timing, quote, migration and integration tests.
- [TRADINGBOT_V2_PART4_CHANGES.md](v2/TRADINGBOT_V2_PART4_CHANGES.md): choices,
  configuration, validation and operational acceptance limits.

## V2 Part 5 contracts and execution

- `Python Scripts/services/contract_policy.py`: versioned rollout modes and
  thresholds for liquidity, scenario selection, risk clusters and exit rungs.
- `Python Scripts/services/liquidity_gate.py`: size/age/spread verdicts, selector
  filtering, canonical reasons and execution-quote validation.
- `Python Scripts/services/scenario_selector.py`: empirical scenarios repriced with Black-Scholes
  (`services/option_pricing.py`),
  independent cost components, P&L quantiles and strict-hybrid-pool ranking.
- `Python Scripts/services/limit_entry.py`: offline bounded entry state machine,
  partial-fill/cancellation rules, price caps and invalidation; no live broker loop.
- `Python Scripts/services/risk_clusters.py` and `config/risk_clusters.csv`:
  maintained assignments and projected position/notional caps.
- `Data/market_db_modules/contract_research.py`: additive company cluster migration,
  immutable observations and market-fill baseline/coverage reports.
- `Python Scripts/services/contract_observations.py`: candidate checkpoint and
  research observation adapters for Part 5 comparisons.
- `Python Scripts/services/position_exit_policy.py`: dated entry/IV inputs,
  catalyst time expiry, volatility collapse and shadow priority comparisons.
- `Python Scripts/services/thesis_capture.py`: classified catalyst holding horizons
  with a labelled legacy DTE fallback; the thesis writer retains fractional days.
- `Python Scripts/trading_support/_option_positions_strategy.py`: new exit rungs
  in the existing risk/expiration/trailing ladder and coactive attribution.
- `scripts/v2_part5.py`: baseline and observation/mapping coverage reports.
- `tests/test_v2_part5.py`: numerical, state-machine, migration and runtime tests.

## V2 Part 6 research infrastructure

- `services/research_contract.py`: common cutoff assertions, research policy and six
  deterministic strategy decisions. Paths here and below are under `Python Scripts/`.
- `services/research_inputs.py`: verified T2 snapshot adapters, mechanical features,
  gate observations and frozen contract selection. The selector's scoped clock in
  `agentCallers/agent_helpers/deterministic_option_selector/market.py` keeps historical
  DTE calculations independent of the current date.
- `services/shadow_portfolio.py`: ask-entry/bid-exit daily counterfactuals, fees,
  coactive exit reasons, gate matrices and three expectancy denominators.
- `Data/market_db_modules/research_schema.sql`, `research_runs.py`: additive run,
  frozen-input, shadow-position, report, promotion and champion storage; exact-N
  enrollment, quote availability checks and same-capital SMH/QQQ comparisons.
- `Data/market_db_modules/research_reports.py`: monthly/full-cohort rankings,
  confidence calibration, cited reports and evidence-qualified promotions.
- `services/research_promotion.py`: policy/source-bound gate approvals and the
  singleton champion's capital guard. Runtime policy factories use this guard.
- `services/catalyst_response_collector.py`: refreshes eligible research runs after
  the existing catalyst/forward-return collection. `agentCallers/main.py` records
  candidate completion before research enrollment.
- `agentCallers/agent_stages/manager_prompt.py`, `manager_normalization.py`,
  `manager_shared.py`: versioned ordinal thesis, timing and execution confidence.
- `scripts/v2_part6.py`: start, collect, report, status and explicit promotion commands.
- `tests/test_v2_part6.py`: cutoff, quote-path, denominator, calibration, freeze,
  coverage and promotion tests. See `TRADINGBOT_V2_PART6_CHANGES.md` for assumptions
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
[Research Phase 3](RESEARCH_PHASE3.md) and `scripts/research.py`.

Cross-cutting infrastructure: `pyproject.toml` declares the import roots (`pip install -e .`);
`services/settings.py` is the one typed settings object, loaded at process start;
`services/entry_gates.py` is the entry-gate registry; `agentCallers/agent_contracts.py` holds the
typed pipeline objects. See [DEVELOPMENT.md](DEVELOPMENT.md).

Phase 4 adds a versioned `evidence_thesis` to the strategist output, deterministic claim checks
(`services/evidence_thesis*.py`), the `EVIDENCE_THESIS` entry gate, manager handoff of the same
checked thesis, and shadow theses for finished research sessions (`services/research_thesis.py`).
Checks persist in `evidence_thesis_checks`. See [Research Phase 4](RESEARCH_PHASE4.md).

Phase 5 treats the manager's call/put and DTE bucket as a proposal. `services/trade_economics.py` reprices the
side-matching contracts with Black-Scholes (`services/option_pricing.py`) on dated empirical catalyst scenarios
and stores an immutable report in `candidate_economics_reports`. It records the promotion-guarded
`TRADE_ECONOMICS` entry gate before the streaming callback, and rechecks the report at order submission.
Policy: `services/trade_economics_policy.py`. See [Research Phase 5](RESEARCH_PHASE5.md).

Phase 6 reviews open positions when a material event arrives. `services/thesis_event_review.py` claims settled
discovery `position_review` items (`claim_position_reviews`) inside the option manager's cycle, and reuses the daily
validator's evidence pack, adjudicator and arbiter. It records shadow-only verdicts, would-be actions and what changed
in `thesis_event_reviews`. Policy: `services/event_thesis_review_policy.py`. See [Research Phase 6](RESEARCH_PHASE6.md).

Phase 7 makes forward evidence accumulate and ties promotion to criteria written beforehand. `begin_candidate` opens a
rolling production cohort (`services/forward_cohorts.py`); while a challenger model pair is on, the live route freezes a
decision observation with every DTE bucket's chain (`services/decision_observations.py`). The collector finalizes the
incumbent's and the challenger's outputs on it (`services/challenger_evaluation.py`, `paired_decisions.py`), and the option
manager polls Alpaca closing marks (`services/research_marks.py`). Paired reports, frozen acceptance criteria and approvals live
in `Data/market_db_modules/forward_comparison.py`; route ledgers in `discovery_evaluation.py` and `research_outcomes.py`.
See [Research Phase 7](RESEARCH_PHASE7.md).

LLM replay backtests (`scripts/project.py llm-backtest`, `agentCallers/llm_backtest.py`) re-ask a manager model, such as a
local llama.cpp model, on every stored decision's frozen input, then run production's continuation, selection, regime and
thesis steps as of the decision time (`services/llm_backtest.py`). Directional answers select from the DTE-bucket chain that
production saved in `candidate_snapshots`; answers in another bucket, and decisions before those snapshots, are reported as
`bucket_chain_unavailable`. Trades are recorded as ordinary backtest runs (`run_decision_backtest`): stored quotes where they
exist, otherwise Black-Scholes marks on daily closes. Answers are cached in `llm_replay_cache`; `--model stored` replays
production's own answers as the like-for-like baseline. Eligibility, the premium cap and portfolio limits come
from each candidate's recorded pipeline manifest; directional answers whose recorded selector settings differ from
the local ones are reported as `selector_config_mismatch`. `--fetch-missing-bars` downloads daily history for
traded symbols that have none. `--min-hours-between N` replays a company's decision only when it is N hours
after the last one replayed for it, chosen by time alone; compare it with a `stored` run using the same N. `--replay-strategist` has the model answer
each decision's frozen strategist input too (`strategist_runs`, by candidate) and hands that answer to
the manager exactly as production does; strategist answers are cached separately. `--score-direction`
(`services/llm_direction_score.py`) grades the same replayed answers by the stock's return over the next 5 and 10
trading-day closes instead of trading options, so decisions from before the bucket-chain snapshots (frozen inputs
start 2026-07-21) can be compared too; it reports how every scored stock moved as the bar a model must beat.
