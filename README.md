# Stock Trading Experiment

An experimental stock-research and trading project focused on combining:

- **market data**
- **news and article gathering**
- **deterministic scoring / filtering**
- and, over time, **agent-driven research and decision support**

This repository is currently a **work-in-progress trading research platform**, not just a finished trading bot. It already contains real infrastructure for collecting market data and scraping financial news, and it is gradually moving toward a more structured pipeline for industry research, company research, evidence scoring, and trade decision support.

---

## What this project is trying to do

The long-term goal of this repo is to build a system that can:

1. gather **market and company data**
2. gather **industry and company news**
3. score and filter that information deterministically
4. organize evidence by company / industry / source quality
5. support a deeper research loop for the most promising opportunities
6. eventually help decide whether a stock should be **bought, sold, or held**

In other words, this project is moving toward a workflow like:

```text
Market / Sector Data
        +
Industry / Company News
        ↓
Filtering + Deduplication + Source Scoring
        ↓
Industry and Company Research
        ↓
Evidence Evaluation
        ↓
Trading / Paper-Trading Decision Support
```

---

## What the repo already does

The repository already contains several useful pieces of infrastructure:

### 1. Financial market-data ingestion
The repo includes a Yahoo Finance-based market-data layer that can:
- fetch company fast info
- fetch sector snapshots
- fetch industry snapshots
- stream stock prices
- store structured sector / industry / company data for later use

### 2. Webscraping pipeline for financial news
The repo includes a more advanced webscraping system than the old README suggests. It already supports:
- deterministic source URL generation
- crawling source/search pages
- extracting article links
- following filtered article links one level deeper
- article-body extraction
- normalization and deduplication support
- failed URL tracking
- evidence-oriented article scoring

### 3. Source-aware extraction
There is already logic for handling and extracting from multiple finance/news sources, with generic extraction plus some source-specific handling for certain sites.

### 4. Stored article metadata and scoring
Saved articles can include scoring-related metadata such as:
- recency
- source reputation
- directness
- factuality
- overall evidence score

### 5. Structured direction toward a research pipeline
The repo is no longer just “fetch prices + fetch articles + train a model.” It is clearly evolving toward a structured research system with:
- industry-level discovery
- company-level research
- evidence tracking
- follow-up research rounds
- stronger source filtering and validation

---

## Current project direction

The current direction of the repo is closer to a **research-first trading system** than a simple bot.

The system is being shaped into something like this:

### Stage 1: Industry discovery
Gather industry-level news and structured market signals to identify promising industries.

### Stage 2: Company candidate selection
Use predefined companies within those industries, plus structured metrics, to choose which companies deserve deeper research.

### Stage 3: Company research
Scrape company-relevant articles, extract useful content, and store scored evidence.

### Stage 4: Follow-up research
Generate targeted follow-up searches/questions and scrape additional evidence in a controlled way.

### Stage 5: Synthesis and decision support
Combine the gathered evidence into a clearer thesis for whether a company looks attractive, risky, or neutral.

---

## Why this repo may look mixed right now

This project is actively evolving, so the repo currently contains a mix of:

- older scripts from an earlier “bot” phase
- newer market-data utilities
- newer webscraping and article-processing infrastructure
- database-backed storage and scoring logic
- work-in-progress architecture for a more structured research loop

That is normal for the current stage of the project. The repo is in transition from a simpler prototype into a better-organized trading research platform.

---

## Main components

## `Python Scripts/Webscraping/`
This is one of the most important parts of the repo right now.

It contains:
- article scraping
- source-page crawling
- source-specific extraction helpers
- source configuration utilities
- scoring logic
- Yahoo Finance data gathering helpers
- company and industry news pipelines

## `Python Scripts/Webscraping/market_data/`
This area focuses on Yahoo Finance-backed market data, sector trees, industry snapshots, and company information.

## `Python Scripts/Webscraping/pipelines/`
This area handles higher-level data gathering flows, such as collecting company news and saving results.

## older trading / execution scripts
The repo also contains older trading-oriented pieces referenced by the original README, including Alpaca-related usage and a simpler bot workflow.

---

## What is finished vs what is still being built

### Already in place
- Yahoo Finance market-data ingestion
- sector / industry / company structured data gathering
- deterministic news-source crawling
- article extraction
- URL filtering and allowlisting
- failed URL tracking
- article reuse from storage
- article scoring and evidence metadata

### Still being improved
- Yahoo Finance news discovery integration
- stronger cross-source confirmation scoring
- follow-up scraping for research loops
- tighter runtime research limits
- better source-specific extractors for weaker sources
- more explicit company/industry research-round storage
- stronger relevance checks after article extraction

---

## Planned improvements

Some of the most important next steps are:

- add **Yahoo Finance news discovery** as a lightweight discovery layer
- keep scraping for **full article text**
- improve **independent-source confirmation**
- add **follow-up query scraping**
- improve **claim-level evidence storage**
- strengthen **post-fetch relevance validation**
- continue consolidating older and newer ingestion paths

---

## Intended use

Right now, this project is best understood as:

- a **learning project**
- a **trading-system experiment**
- a **research/data pipeline prototype**
- and a foundation for future agent-assisted trading research

It should **not** be treated as production trading software.

---

## Setup notes

The repo has evolved beyond the original setup instructions, but the older README still reflects the early project shape.

Historically, the project used:
- **Alpaca API** for trading
- **NewsMesh API** for article discovery
- local key storage through a `Keys.py` file

Depending on which part of the repo you are running, you may still need API keys and local configuration files.

At minimum, expect to configure:
- trading credentials if using Alpaca-related scripts
- news/API credentials if using older NewsMesh-based scripts
- source configuration for the newer scraping pipeline
- local database / data directories used by the scraping and market-data systems

---

## Recommended future README split

As the repo matures, it will probably make sense to split this README into:
- a top-level project overview
- a setup guide
- a scraping/data-ingestion guide
- a trading/execution guide
- a research-pipeline architecture doc

For now, this README is meant to help visitors understand the **purpose and direction** of the project.

---

## Disclaimer

This repository is an experiment and research project. Nothing here should be taken as financial advice, investment advice, or a recommendation to trade live capital.

---

## Original project background

The original README described the project as a stock trading bot using Alpaca for trades and NewsMesh for articles, with the data feeding into an ML model for trade decisions. That background is still useful context, but the repo has grown beyond that earlier description and now includes a much richer data-gathering and research pipeline. 
