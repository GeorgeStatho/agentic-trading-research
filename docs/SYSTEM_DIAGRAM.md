# System Diagram

How the whole repository fits together, drawn from the code as it runs today.
Each section zooms in on one part of the system; the file and function names in
the nodes are where to start reading. See [ARCHITECTURE.md](ARCHITECTURE.md) for
where new code belongs and [CODE_MAP.md](CODE_MAP.md) for the folder map.

1. [Runtime topology](#1-runtime-topology) — containers, external services, shared state
2. [News collector loop](#2-news-collector-loop) — scrape, classify, rank, collect evidence
3. [Worker trading loop](#3-worker-trading-loop) — schedule, exposure caps, streaming vs batch
4. [Per-company decision pipeline](#4-per-company-decision-pipeline) — strategist → manager → gates → order
5. [Order execution guardrails](#5-order-execution-guardrails) — what can still stop a buy
6. [Option position management](#6-option-position-management) — exits, thesis validation, outcomes
7. [Research and discovery (shadow)](#7-research-and-discovery-shadow) — never places orders
8. [Data layer](#8-data-layer) — SQLite modules and import surfaces
9. [API and dashboard](#9-api-and-dashboard) — what the UI reads
10. [LLM provider layer](#10-llm-provider-layer) — which stages call a model

---

## 1. Runtime topology

Docker Compose ([docker-compose.yml](../docker-compose.yml)) runs every Python
service from the same image (`Dockerfile.worker`). They share one SQLite file on
the `db_data` volume and exchange status JSON on the `runtime_data` volume.

```mermaid
flowchart LR
    user(["Browser"])

    subgraph ext["External services"]
        alpaca[("Alpaca<br/>paper trading + option data")]
        llm[("LLM providers<br/>Vertex AI / OpenAI / Ollama")]
        yf[("Yahoo Finance<br/>yfinance")]
        web[("News sites + RSS<br/>CNBC, Yahoo, Barron's, Fool,<br/>MarketWatch, Investing, Morningstar")]
        sec[("SEC EDGAR +<br/>issuer releases")]
        mdapp[("MarketData.app<br/>EOD option quotes")]
        tele[("Telemetry receiver<br/>TELEMETRY_ENDPOINT")]
    end

    subgraph compose["docker compose"]
        nginx["web<br/>nginx + built React app<br/>:80 / :443, read-only API"]
        certbot["certbot<br/>renews TLS every 12h"]
        api["api<br/>api.py (Flask :5000)"]
        worker["worker<br/>Python Scripts/main.py"]
        optmgr["option_manager<br/>option_manager_main.py"]
        news["news_collector<br/>news_collector_main.py"]
        eod["marketdata_eod_sync<br/>marketdata_eod_sync_main.py"]
        ollama["ollama (profile: ollama)<br/>+ ollama-init pulls llama3.1"]
    end

    subgraph vols["Volumes"]
        db[("db_data<br/>stock_experiment.db (SQLite)")]
        shared[("runtime_data /shared<br/>script_status.json<br/>option_manager_status.json<br/>trade_execution_output.json<br/>agent_runner_output.json<br/>option_position_management_output.json<br/>agent_pipeline_output.json")]
    end

    user -->|HTTPS| nginx
    nginx -->|/api/*| api
    certbot -.-> nginx

    news -->|scrape| web
    news -->|sectors, industries,<br/>companies, prices| yf
    news -->|filings, releases| sec
    news -->|classify articles| llm
    news --> db
    news --> shared

    worker -->|market clock, account,<br/>option chains, orders| alpaca
    worker -->|strategist + manager| llm
    worker -->|bars, regime inputs| yf
    worker <--> db
    worker --> shared

    optmgr -->|positions, exit orders| alpaca
    optmgr -->|thesis adjudicator| llm
    optmgr <--> db
    optmgr --> shared

    eod -->|daily 10:00 ET,<br/>credit-budgeted| mdapp
    eod <--> db

    api --> db
    api --> shared
    api -->|portfolio history,<br/>positions| alpaca
    api -->|Exposure Map runs| llm

    worker & optmgr & news & api -.->|startup ping| tele
    llm -.->|LLM_PROVIDER=ollama| ollama
```

---

## 2. News collector loop

`news_collector_main.py` runs every `NEWS_COLLECTION_INTERVAL_SECONDS` (3h by
default). The scrape itself is in `agent_pipeline/news_refresh.py`
(`run_news_collection_pipeline`); each scrape runs in a subprocess so a crashing
browser can't take down the loop. Everything the worker trades on later is
written to the DB here.

```mermaid
flowchart TD
    start(["news_collector_main.main_loop()"]) --> ready["StartupReadinessChecker<br/>(LLM reachable, output paths writable)"]
    ready --> cold{"COLD_START_SANITY_CHECK?"}
    cold -->|yes| boot["init market + news DBs<br/>seed sectors from Data/sector_etfs.json<br/>ensure_all_sector_market_data() via yfinance<br/>(industries + companies)"]
    cold -->|no| regime
    boot --> regime

    subgraph cycle["run_news_collection_cycle()"]
        direction TB
        regime["build_market_regime_snapshot()<br/>SPY / VIX / macro calendar → DB"]
        subgraph pipe["run_news_collection_pipeline()"]
            direction TB
            us["USNewsPipeline.get_us_news_from_rss"]
            world["worldNewsPipeline.get_world_news_from_rss"]
            macro["LLM: macro_news_to_sectors<br/>(scope = us, world)<br/>→ sector impact pairs"]
            rank1["ranking.get_current_rankings()<br/>top N sectors & industries<br/>(PIPELINE_RANKING_MAX_AGE_DAYS)"]
            subgraph perSector["for each top sector"]
                direction TB
                sscrape["sectorNewsPipeline (RSS)"]
                sopp["LLM: sector_opportunist<br/>classify_sector_articles"]
                iopp["LLM: industry_opportunist<br/>sector articles → industries"]
                subgraph perIndustry["for each top industry in sector"]
                    direction TB
                    iscrape["industryNewsPipeline"]
                    pick["target_selection<br/>collect_ranked_companies_for_industry"]
                    demand["request_pipeline_evidence<br/>(queue company evidence)"]
                    subgraph perCompany["for each top company"]
                        cscrape["companyNewsPipeline"]
                        copp["LLM: company_opportunist<br/>classify_company_articles"]
                    end
                end
            end
            rank2["rankings after pipeline"]
            us --> world --> macro --> rank1 --> perSector --> rank2
            sscrape --> sopp --> iopp --> perIndustry
            iscrape --> pick --> demand --> perCompany
            cscrape --> copp
        end
        catalyst["catalyst_response_collector<br/>price responses, forward returns,<br/>refresh active research runs"]
        evidence["company_evidence_service<br/>SEC facts, filings, issuer releases<br/>→ immutable evidence packages"]
        forward["forward_evaluation (shadow)<br/>challenger evaluations, research-thesis<br/>outcomes, daily comparison reports"]
        discover["event_discovery.discover_company_events<br/>→ discovery queue (shadow)"]
        out["write agent_pipeline_output.json"]
        regime --> pipe --> catalyst --> forward --> evidence --> discover --> out
    end

    out --> sleep["sleep NEWS_COLLECTION_INTERVAL_SECONDS"] --> regime
```

Inside every `*NewsPipeline` call above, the `Python Scripts/Webscraping` package
does the fetching and parsing:

```mermaid
flowchart LR
    src["sources/<br/>source_config, urlFactories"] --> jobs["pipelines/job_builder<br/>+ _orchestration, _sector_rss"]
    jobs --> eng["engine/<br/>article_scraper,<br/>playwright_runner (rendered fallback)"]
    eng --> exx["extractors/<br/>cnbc, yahoo, barrons, fool,<br/>marketwatch, investing, morningstar"]
    exx --> proc["processing/<br/>news_normalization, scoring"]
    proc --> ndb[("news_db_modules<br/>article_store, provenance roots,<br/>catalysts")]
    yfn["market_data/<br/>yFinanceNews, yFinanceData"] --> proc
```

---

## 3. Worker trading loop

`Python Scripts/main.py` wires the services together and hands off to
`WorkerApplication.run_main_loop()`
([services/runtime/worker_application.py](../Python%20Scripts/services/runtime/worker_application.py)).
The worker reads the pipeline state the collector already wrote; it does not scrape.

```mermaid
flowchart TD
    boot(["main.py → WorkerApplication.run_main_loop()"]) --> rc["StartupReadinessChecker"]
    rc --> cs["ColdStartSanityChecker (optional)"]
    cs --> recon["position_manager.reconcile_outcomes()<br/>(broker round trips → decision outcomes)"]
    recon --> loop

    loop{"Alpaca clock:<br/>market open?"}
    loop -->|no| closed["status = paused 'Market is closed'<br/>sleep until next open"] --> loop
    loop -->|yes| pm{"AUTO_MANAGE_OPTION_POSITIONS<br/>and management due?"}
    pm -->|yes| pmrun["position_manager.run_cycle()<br/>(see §6)"] --> tdue
    pm -->|no| tdue{"trading cycle due?<br/>(RUN_INTERVAL_SECONDS)"}
    tdue -->|no| nap
    tdue -->|yes| cool{"inside market-open<br/>entry cooldown?"}
    cool -->|yes| pause["paused: market_open_entry_cooldown"] --> nap
    cool -->|no| exp["OptionExposureSnapshot<br/>buying power + open option exposure"]
    exp --> cap{"exposure cap<br/>already consumed?"}
    cap -->|yes| skip["skip cycle, status paused"] --> nap
    cap -->|no| mode{"IMMEDIATE_OPTION_EXECUTION?"}

    mode -->|yes| stream["run_streaming_trading_cycle()<br/>execute each manager result<br/>as soon as it is produced"]
    mode -->|no| batch["run_trading_cycle()<br/>run all companies, then execute"]

    stream --> fri
    batch --> fri
    fri{"Friday in<br/>America/New_York?"}
    fri -->|yes| nobuy["skip whole cycle:<br/>no new option entries"]
    fri -->|no| agents["agentCallers.decision_pipeline<br/>run_full_agent_stack_from_existing_data()<br/>(see §4)"]
    agents --> cand["OrderCandidateBuilder<br/>entry_allowed(all ENTRY_GATES)<br/>+ catalyst admission<br/>+ confidence high (or medium if enabled)"]
    cand --> exec["OptionTradeExecutor.execute / execute_candidate<br/>(see §5)"]
    exec --> persist["write agent_runner_output.json,<br/>selected_options_output.json,<br/>trade_execution_output.json"]
    nobuy --> persist
    persist --> nap["sleep until next due task<br/>status = paused 'Sleeping'"] --> loop
```

---

## 4. Per-company decision pipeline

`_run_strategist_and_manager()` in
[agentCallers/decision_pipeline.py](../Python%20Scripts/agentCallers/decision_pipeline.py). LLMs only
explain and classify grounded inputs; every go/no-go is a deterministic gate.
Every step writes a `candidate_lifecycle` checkpoint so a decision can be replayed
and audited later.

```mermaid
flowchart TD
    targets["run_agent_pipeline_from_existing_data()<br/>current sector → industry → company targets from DB"] --> dshadow["run_discovery_shadow_cycle()<br/>(shadow only, see §7)"]
    dshadow --> filt["_filter_company_symbols_by_high_confidence_support<br/>≥ MIN high-confidence recent articles"]
    filt --> prep["once per cycle:<br/>build_market_regime_snapshot()<br/>request_pipeline_evidence(scope=manager)<br/>build_pipeline_version() (config/model/prompt hashes)<br/>EvidenceThesisPolicy.from_env()"]
    prep --> each

    subgraph each["for each company symbol"]
        direction TB
        disc{"discovery-shadow<br/>candidate?"} -->|yes| skipd["skip (never traded)"]
        disc -->|no| begin["begin_candidate → candidate_id<br/>(opens the forward cohort first)"]
        begin --> adm{"catalyst_admission_allowed?<br/>(provenance, freshness decay)"}
        adm -->|no| blocked["checkpoint admission_blocked"]
        adm -->|yes| strat

        subgraph strat["STRATEGIST — agent_stages/strategist_runner.decide_company_purchase"]
            direction TB
            sp["agent_builders/strategist_payload*<br/>articles, company facts, price,<br/>sector/industry rollups, regime,<br/>company evidence package"]
            sp --> sllm["LLM (STRATEGIST_MODEL)<br/>JSON schema: buy / do_not_buy,<br/>confidence, evidence_thesis"]
            sllm --> sg["strategist_grounding<br/>validate cited article ids"]
            sg --> sn["strategist_normalization<br/>+ evidence_thesis checks"]
            sn --> ssave["record_strategist_run,<br/>add_strategist_company_summary"]
        end

        strat --> mgr

        subgraph mgr["MANAGER — agent_stages/manager_runner.decide_company_option_position"]
            direction TB
            mp["agent_helpers/manager.build_manager_input<br/>+ market_context: underlying bars & indicators,<br/>IV history, option chain (Alpaca), account"]
            mp --> ev{"payload has<br/>evidence?"}
            ev -->|no| noev["no-evidence result (neither)"]
            ev -->|yes| sd{"strategist answered<br/>watchlist / do_not_trade?"}
            sd -->|yes| noev2["strategist-declined result (neither),<br/>no model call"]
            sd -->|no| mllm["LLM (MANAGER_MODEL)<br/>call / put / neither,<br/>thesis/timing/execution confidence,<br/>target DTE bucket"]
            noev2 --> cont
            mllm --> mnorm["manager_normalization"]
            mnorm --> cont["evaluate_evidence_continuation_gate<br/>→ entry_gate (CONTINUATION)"]
            noev --> cont
        end

        mgr --> gates
        mgr -. "challenger on" .-> freeze["freeze decision observation<br/>model inputs + every bucket's chain<br/>(shadow, §7)"]

        subgraph gates["_apply_post_manager_gates (deterministic)"]
            direction TB
            hurdle["option_hurdle_gate<br/>expected vs implied move (shadow)"]
            scen["scenario_selector.prepare_scenario_inputs"]
            sel["deterministic_option_selector<br/>filters → greeks → volatility →<br/>simple / hybrid ranking,<br/>liquidity_gate"]
            reg["market_regime_gate<br/>→ REGIME verdict"]
            thes["evaluate_evidence_thesis_gate<br/>→ EVIDENCE_THESIS verdict"]
            econ["trade_economics (finalizer)<br/>Black-Scholes on empirical catalyst scenarios<br/>→ immutable report + TRADE_ECONOMICS verdict"]
            hurdle --> scen --> sel --> reg --> thes --> econ
        end

        gates --> hist["record_manager_decision_history"]
        hist --> cb["on_manager_result callback<br/>(streaming mode executes now)"]
        cb --> done["checkpoint candidate_complete"]
    end

    each --> result["selected_options output<br/>strategist[], manager[], failed_companies[]"]
    each -. "MAX_CONSECUTIVE_COMPANY_FAILURES<br/>→ stop cycle (shared outage)" .-> result
```

The gate registry is [services/gates/entry_gates.py](../Python%20Scripts/services/gates/entry_gates.py):

| Gate | Result key | Missing verdict |
| --- | --- | --- |
| `CONTINUATION` | `entry_gate` | blocks |
| `REGIME` | `market_regime_gate` | blocks |
| `EVIDENCE_THESIS` | `evidence_thesis_gate` | allowed (optional gate) |
| `TRADE_ECONOMICS` | `trade_economics_gate` | allowed (optional gate; attached to every directional result while on) |

---

## 5. Order execution guardrails

`OptionTradeExecutor` in
[services/trading/trade_executor.py](../Python%20Scripts/services/trading/trade_executor.py).
A candidate that passed every entry gate can still be skipped here.

```mermaid
flowchart TD
    c(["order candidate"]) --> champ{"research champion<br/>guard allows?"}
    champ -->|no| r0["skip: research_champion_only"]
    champ -->|yes| cat{"catalyst admission?"}
    cat -->|no| r1["skip: catalyst_admission_blocked"]
    cat -->|yes| px["reference price from selected option<br/>→ size qty from buying power"]
    px --> prem{"premium ≤ per-trade cap?"}
    prem -->|no| r2["skip: premium_per_trade_cap"]
    prem -->|yes| port{"portfolio guardrails, in order:<br/>daily realized loss limit<br/>max concurrent positions<br/>total entry notional<br/>sector notional %<br/>risk cluster (config/risk_clusters.csv)<br/>per-symbol entry attempts"}
    port -->|hit| r3["skip: portfolio_&lt;guardrail&gt;"]
    port -->|ok| liq{"fresh quote passes<br/>V2 liquidity checks?"}
    liq -->|no| r4["skip: execution_liquidity_blocked"]
    liq -->|yes| econ{"trade-economics report still valid?<br/>unexpired, event unchanged, spot within 0.5 ATR,<br/>repriced contract still positive"}
    econ -->|no, enforced| r6["skip: economics_recheck_failed"]
    econ -->|yes / shadow| lim["limit_entry shadow plan (recorded only)"]
    lim --> sub["AlpacaTradingGateway<br/>submit_option_market_order"]
    sub -->|error| r5["not submitted, error recorded"]
    sub -->|ok| rec["OptionTradeJournal → option_trade_executions<br/>PositionThesisJournal → entry thesis<br/>link manager decision history<br/>execution telemetry (quote, fill)"]
    rec --> ck["checkpoint order_submitted"]
```

---

## 6. Option position management

Runs in the dedicated `option_manager` container (`run_option_manager_loop`),
and in the worker too if `WORKER_AUTO_MANAGE_OPTION_POSITIONS=true`.
`OptionPositionManagerService.run_cycle()` in
[services/options/position_manager.py](../Python%20Scripts/services/options/position_manager.py)
drives `trading_support/_option_positions_*`.

```mermaid
flowchart TD
    start(["option_manager_main → run_option_manager_loop()"]) --> recon["reconcile_outcomes()<br/>outcome_reconciliation: closed broker round trips"]
    recon --> open{"market open?"}
    open -->|no| wait["paused, sleep"] --> open
    open -->|yes| snap["snapshot open option positions<br/>+ quotes, DTE, P/L, momentum history"]
    snap --> ladder

    subgraph ladder["exit ladder — _option_positions_strategy (first matching rung wins)"]
        direction TB
        e0["data_invalid / no_position_qty /<br/>recent_fill_reconciliation / pending_exit_order → hold"]
        e1["stop_loss<br/>(with underlying-hold and pending-confirmation variants)"]
        e2["near_expiration<br/>(entry bucket's forced-exit DTE)"]
        e3["static_take_profit"]
        e4["trailing_profit_stop"]
        e5["first / second scale-out trigger<br/>(partial sells)"]
        e6["V2 rungs: catalyst time expiry,<br/>volatility collapse (position_exit_policy)"]
        e7["hold"]
        e0 --> e1 --> e2 --> e3 --> e4 --> e5 --> e6 --> e7
    end

    ladder --> orders["Alpaca: limit sell (spread-aware tick rounding)<br/>or market sell / close position;<br/>cancel stale exit orders"]
    orders --> jr["trade journal: record_position_exit<br/>thesis journal: record_exit<br/>refresh decision-history P/L<br/>position_telemetry (extrema, exit attribution)"]
    jr --> tv

    subgraph tv["ThesisValidatorService.run_due_validation"]
        direction TB
        pack["thesis_evidence_pack<br/>(entry thesis + new articles)"] --> adj["LLM: thesis_adjudicator<br/>multiple grounded samples<br/>+ consistency gate"]
        adj --> arb["thesis_arbiter<br/>hold / close half / close"]
    end

    tv --> er

    subgraph er["EventThesisReviewService.run (shadow, Phase 6)"]
        direction TB
        claim["claim 1 settled position_review item<br/>(discovery queue, 15-min debounce)"] --> skip{"rules exit, working order,<br/>no new evidence, same evidence,<br/>daily limit?"}
        skip -->|no| epack["event evidence pack<br/>(trigger articles first,<br/>previous verdict, Phase 4 thesis)"]
        epack --> eadj["LLM: thesis_adjudicator"] --> earb["thesis_arbiter →<br/>would-be action only"]
        skip -->|yes| erec["record status, no model call"]
        earb --> erec2["thesis_event_reviews<br/>(what changed and why)"]
    end

    er --> marks["research closing marks (shadow, ~10 min before close)<br/>Alpaca quotes for open research positions"]
    marks --> outw["write option_position_management_output.json<br/>status → option_manager_status.json"]
    outw --> open
```

---

## 7. Research and discovery (shadow)

These subsystems measure and compare strategies. They are disabled or
shadow-only by default and **do not feed broker execution**; the only link back to
live trading is the research-promotion champion guard checked in §5. See
[RESEARCH_PHASE0–7](README.md) and `scripts/v2_part*.py`. Paired forward evaluations
(`paired:` candidates) and discovery intake (`discovery:`) are refused by the order
builder and the champion guard.

```mermaid
flowchart LR
    subgraph ingest["Collector-side inputs"]
        ce["company_evidence_service<br/>company_evidence/ (sec, sec_facts,<br/>issuer, releases)"]
        ed["event_discovery<br/>primary disclosures + provenance"]
        crc["catalyst_response_collector<br/>price_response: betas, residuals, MFE/MAE"]
    end

    subgraph queue["Discovery"]
        dq[("discovery_queue")]
        dcons["discovery_queue.run_discovery_shadow_cycle<br/>discovery_admission"]
        dcand[("discovery_candidates<br/>frozen research inputs")]
    end

    subgraph investigate["Bounded investigation"]
        ro["research_orchestrator<br/>durable sessions, daily allowance,<br/>research_lock"]
        rp["LLM: research_planner<br/>(typed archive-retrieval plan)"]
        rt["research_tools (read-only ops)"]
        rth["research_thesis / research_thesis_writer<br/>shadow evidence theses"]
    end

    subgraph measure["Measurement + promotion"]
        lc["candidate_lifecycle<br/>checkpoints, rejections, funnels"]
        sgates["shadow_gates / catalyst & pricing<br/>gate observations"]
        sp["shadow_portfolio<br/>ask-entry / bid-exit counterfactuals"]
        rr["research_runs + research_reports<br/>cohorts, calibration, expectancy"]
        prom["research_promotion<br/>policy/source-hashed approvals<br/>singleton champion"]
    end

    subgraph forward["Forward evaluation (Phase 7)"]
        cohorts[("research_run_acceptance<br/>rolling cohorts, frozen criteria")]
        dobs[("decision_observations<br/>frozen inputs + bucket chains")]
        chal["challenger_evaluation<br/>incumbent + challenger finalized<br/>as of the cutoff"]
        cmp["forward_comparison<br/>paired report, cluster bootstrap,<br/>acceptance, approval"]
        ledgers["discovery_evaluation · research_outcomes<br/>route ledgers"]
    end

    subgraph offline["Offline tools"]
        bt["backtesting<br/>(market_db_modules/backtesting,<br/>agentCallers/backtest.py)"]
        eod["marketdata_sync<br/>EOD option quote cache"]
        replay["offline_manager_evaluation<br/>manager_replay"]
    end

    ed --> dq --> dcons --> dcand --> ro
    dq -. "position_review items" .-> evrev(["event thesis reviews (§6)"])
    ce --> ro
    ro --> rp --> rt --> ro
    ro --> rth
    crc --> rr
    lc --> rr
    sgates --> rr
    sp --> rr
    rr --> prom
    dobs --> chal --> rr
    cohorts --> rr
    rr --> cmp
    ro --> ledgers
    prom -. "champion guard" .-> exec(["OptionTradeExecutor (§5)"])
    eod --> bt
    lc --> replay
```

---

## 8. Data layer

One SQLite file (`DB_PATH`). Schemas are additive, idempotent
`CREATE ... IF NOT EXISTS` migrations applied at startup.

```mermaid
flowchart TD
    callers["pipelines, agent stages, services,<br/>api_support, scripts/*.py"] --> helpers

    subgraph helpers["Shared"]
        dbc["Data/db_common.py<br/>(current database, connection, setup)"]
    end

    subgraph mmods["Data/market_db_modules/"]
        direction TB
        m1["entities, snapshots, loaders, queries<br/>sectors · industries · companies · rankings"]
        m2["trade_executions, history_records/links,<br/>position_outcomes, execution_telemetry,<br/>manager_replay, autopsy, portfolio_risk"]
        m3["candidate_lifecycle, shadow_gates,<br/>catalyst_research, pricing_research,<br/>contract_research, market_regime"]
        m4["company_evidence, company_collection,<br/>company_interest, evidence_refresh,<br/>evidence_thesis(_report), thesis_validator"]
        m5["discovery_queue/sources/candidates,<br/>research_foundation/runtime/runs/reports/work,<br/>research_observations, foundation_store"]
        m6["backtesting, backtest_imports,<br/>backtest_marketdata, marketdata_sync,<br/>marketdata_budget, refresh_state"]
    end

    subgraph nmods["Data/news_db_modules/"]
        direction TB
        n1["article_store, entity_articles,<br/>links"]
        n2["macro, impacts, processing<br/>(opportunist classifications)"]
        n3["provenance (source roots),<br/>catalysts (state + decay)"]
        n4["strategist_runs"]
    end

    mmods & nmods --> dbc

    subgraph schema["Schemas"]
        s1["market_schema.sql + *_schema.sql modules"]
        s2["news_schema.sql"]
        s3["catalyst_schema.sql / catalyst_models.py"]
    end
    mmods --> sqlite[("stock_experiment.db")]
    nmods --> sqlite
    schema --> sqlite
```

---

## 9. API and dashboard

The React app (`web_dashboard/src`, built with Vite, served by nginx) calls the
Flask API. Routes live in `api_routes/` (request parsing only) and delegate to
`api_support/` (data shaping, no Flask objects).

```mermaid
flowchart LR
    subgraph ui["web_dashboard/src (routes.tsx)"]
        direction TB
        live["Live / Overview<br/>dashboardHome: KPIs, status,<br/>portfolio graph, open positions,<br/>risk controls, why-bot-traded, orders"]
        dec["Decisions<br/>Ledger · Autopsy · Company News ·<br/>Exposure Map"]
        res["Research<br/>Expectancy · Backtests · V2 Rollout"]
        demo["Demo Mode (?demo=1)<br/>static fixture, read-only"]
    end

    subgraph routes["api_routes/* → api_support/*"]
        direction TB
        a1["dashboard<br/>/script-status /dashboard-kpis /open-positions<br/>/risk-controls /market-regime<br/>/company-decisions /decision-expectancy"]
        a2["trades<br/>/trade-execution-output /executed-trades<br/>/why-bot-traded"]
        a3["health<br/>/health /portfolio-history"]
        a4["news<br/>/opportunist-company-news"]
        a5["exposure_map<br/>/exposure-map/articles, /&lt;id&gt;, POST /runs"]
        a6["decision_autopsy /decision-autopsies"]
        a7["backtest /backtests, POST /backtests,<br/>POST /backtests/marketdata-eod,<br/>POST /backtests/alpaca-minute-bars"]
        a8["thesis_validator · v2_status · forward_evaluation ·<br/>demo_mode · telemetry (POST /startup, GET /summary)"]
    end

    live --> a1 & a2 & a3
    dec --> a1 & a4 & a5 & a6
    res --> a1 & a7 & a8
    demo --> a8

    a1 --> shared[("/shared status JSON")]
    a1 & a2 & a4 & a5 & a6 & a7 & a8 --> db[("SQLite")]
    a3 --> alp[("Alpaca")]
    a5 --> llm[("LLM (OpenAI/Vertex)")]
    a7 --> md[("MarketData.app<br/>(budgeted)")]
    a7 --> alpacabars[("Alpaca 1-min<br/>stock + option bars")]
```

---

## 10. LLM provider layer

All model calls go through `agent_helpers/llm_client.py`
(`get_model_client`, `ask_llm_model`; the providers live in `llm_vertex.py` and `llm_openai.py`,
errors in `llm_errors.py`, usage records in `llm_usage.py`), which picks Vertex AI, the Gemini API (AI Studio key),
OpenAI, llama.cpp or Ollama from each stage's `<STAGE>_PROVIDER`, falling back to `LLM_PROVIDER`,
and the per-stage model settings in `services.core.settings.get_settings()`. The news classifiers call
`ask_classifier_model()`, which moves a quota-refused call down the `CLASSIFIER_FALLBACKS` models in order and returns
the model that answered, for storage with the classification.

```mermaid
flowchart LR
    subgraph callers["Model-calling stages"]
        direction TB
        s1["macro_news_to_sectors<br/>WORLD_/MACRO_NEWS_MODEL"]
        s2["sector_opportunist<br/>SECTOR_OPPURUNTIST_MODEL"]
        s3["industry_opportunist<br/>INDUSTRY_OPPURUNTINST_MODEL"]
        s4["company_opportunist<br/>COMPANY_OPPURUNTIST_MODEL"]
        s5["strategist<br/>STRATEGIST_MODEL"]
        s6["manager<br/>MANAGER_MODEL"]
        s7["thesis_adjudicator"]
        s8["research_planner<br/>(own transport module)"]
        s9["api exposure_map"]
        s10["startup_readiness ping"]
        s11["challenger strategist/manager (shadow)<br/>RESEARCH_CHALLENGER_*_MODEL"]
    end
    callers --> shared["agent_helpers/llm_client.py<br/>ask_llm_model()<br/>system-prompt caching (Vertex),<br/>rate-limit handling, diagnostics,<br/>extract_json_value()"]
    shared -->|vertex| v[("Vertex AI (Gemini)")]
    shared -->|gemini| g[("Gemini API<br/>AI Studio key")]
    shared -->|openai_compatible| oc[("Chat Completions API<br/>Mistral / Groq / OpenRouter")]
    g -. "quota used up<br/>(news classifiers only)" .-> oc
    shared -->|openai| o[("OpenAI Responses API")]
    shared -->|ollama| l[("Ollama llama3.1")]
    shared --> val["stage validation<br/>JSON schema, grounding of cited ids,<br/>normalization"]
```
