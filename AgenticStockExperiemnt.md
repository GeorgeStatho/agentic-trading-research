# Stock Trading App Structure

## Overview

This application is an **industry-first, evidence-driven, multi-round stock research system**.

The goal is to:

* scan industries for opportunity,
* select the most promising industries,
* identify relevant companies within those industries,
* research those companies using scraped web data,
* ask targeted follow-up questions,
* refine the research,
* apply risk controls,
* and finally produce a **buy / sell / hold** decision.

The system should use **deterministic code for data collection, filtering, scoring, and execution**, while using **LLM agents for interpretation, ranking, synthesis, and question generation**.

---

## High-Level Flow

```text
Industry News Scraper
    ↓
Industry Analyst Agent
    ↓
Top Industries
    ↓
Ticker Selector / Candidate Ranker
    ↓
Company Article Scraper
    ↓
Researcher Agent
    ↓
Questioning Agent
    ↓
Follow-up Search Generator
    ↓
Scraper
    ↓
Researcher Agent (Round 2)
    ↓
Thesis / Synthesis Agent
    ↓
Risk Manager
    ↓
Decision Agent
    ↓
Execution Code
```

---

## Design Principles

### 1. Deterministic code should control:

* scraping,
* ticker universes,
* filtering,
* ranking pre-scores,
* source validation,
* risk limits,
* trade execution.

### 2. Agents should control:

* interpretation of evidence,
* prioritization,
* synthesis,
* targeted question generation,
* final recommendation framing.

### 3. Every stage should return structured output.

Prefer JSON-like schemas instead of free-form text.

### 4. Research loops must be limited.

Suggested defaults:

* max 2 research rounds,
* max 2–3 follow-up questions per company per round.

### 5. Evidence should be traceable.

Every important claim should be tied to:

* source URL,
* source type,
* timestamp,
* confidence,
* supporting excerpt or summary.

---

## Stage 0: Static Definitions

Define the stable knowledge your app uses before runtime.

### Includes

* industry list,
* ticker lists per industry,
* optional subindustry mapping,
* allowed sources/domains,
* article recency rules,
* maximum research rounds,
* risk rules.

### Example

```json
{
  "industries": {
    "semiconductors": ["NVDA", "AMD", "INTC", "AVGO", "TSM"],
    "oil_and_gas": ["XOM", "CVX", "COP", "SLB"],
    "cybersecurity": ["CRWD", "PANW", "ZS", "FTNT"]
  }
}
```

### Purpose

This prevents the system from hallucinating industry-company relationships.

---

## Stage 1: Industry Discovery

### Goal

Find which industries currently show the strongest opportunity.

### Inputs

* recent industry-related news,
* macro/economic news,
* sector ETF movement,
* optional commodity/rate data depending on industry.

### Process

1. Scrape news for each industry.
2. Deduplicate and clean the articles.
3. Pass the industry article bundle to the **Industry Analyst Agent**.
4. Score and rank industries.

### Suggested Output

```json
{
  "industry": "semiconductors",
  "opportunity_score": 82,
  "direction": "bullish",
  "confidence": 0.74,
  "key_drivers": [
    "AI infrastructure demand remains strong",
    "Supply chain constraints easing"
  ],
  "key_risks": [
    "Valuation stretched",
    "Export restrictions risk"
  ],
  "reason_to_research": "Industry has strong catalyst density and multiple affected large-cap firms."
}
```

### Recommendation

Carry forward the **top 2 or top 3 industries**, not just one.

---

## Stage 2: Company Candidate Selection

### Goal

Choose which companies in the selected industries deserve deeper research.

### Inputs

* selected industries,
* predefined ticker list for each industry,
* ticker mention counts in recent articles,
* price movement,
* volume,
* earnings timing,
* liquidity metrics.

### Process

1. Deterministic code computes a candidate score.
2. A **Company Prioritizer Agent** ranks the most research-worthy companies.

### Suggested Output

```json
{
  "industry": "semiconductors",
  "selected_companies": [
    {
      "ticker": "NVDA",
      "priority": 1,
      "why_selected": "Highest catalyst density and strongest connection to industry-wide demand story"
    },
    {
      "ticker": "AMD",
      "priority": 2,
      "why_selected": "Peer exposure with lower relative valuation narrative"
    }
  ]
}
```

### Recommendation

Use deterministic pre-scores first, then let the agent rank the shortlist.

---

## Stage 3: Researcher Agent

### Goal

Produce a company-specific evidence summary from scraped articles and structured data.

### Inputs

For each company:

* article text,
* URL,
* title,
* timestamp,
* source type,
* source quality,
* market metrics if available.

### Process

The **Researcher Agent** should extract:

* what happened,
* why it matters,
* bullish and bearish interpretations,
* missing information,
* evidence quality.

### Suggested Output

```json
{
  "ticker": "NVDA",
  "summary": "Recent coverage suggests continued AI demand strength and strong enterprise GPU demand.",
  "bullish_points": [
    "Large data-center demand tailwind",
    "Positive partner commentary"
  ],
  "bearish_points": [
    "Valuation concerns",
    "Potential regulatory/export risk"
  ],
  "evidence_items": [
    {
      "claim": "AI demand is rising",
      "support": "Article cites increased enterprise orders",
      "url": "https://...",
      "source_type": "news_article",
      "confidence": 0.72
    }
  ],
  "open_questions": [
    "How much of demand is already priced in?",
    "Are competitors gaining share?"
  ],
  "company_score": 76
}
```

### Recommendation

Require every important claim to be linked to evidence.

---

## Stage 4: Questioning Agent

### Goal

Identify the most important missing information that would improve the stock thesis.

### Inputs

* company summary,
* evidence items,
* open questions,
* optional decision rubric.

### Process

The **Questioning Agent** should not browse directly.
It should only generate:

* follow-up questions,
* search queries,
* preferred source types.

### Suggested Output

```json
{
  "ticker": "NVDA",
  "questions": [
    {
      "question": "Is recent demand growth broad-based or concentrated in a few hyperscalers?",
      "why_it_matters": "Concentration risk affects durability of thesis",
      "search_query": "NVDA hyperscaler demand concentration latest news",
      "preferred_sources": ["earnings commentary", "reputable financial news"]
    },
    {
      "question": "Are export restrictions likely to impact near-term sales?",
      "why_it_matters": "Could reduce forward revenue expectations",
      "search_query": "NVDA export restrictions impact revenue guidance"
    }
  ],
  "stop_research": false
}
```

### Recommendation

Limit the number of questions and stop once new information adds little value.

---

## Stage 5: Follow-up Scraping

### Goal

Fetch targeted information that answers the questions generated in Stage 4.

### Inputs

* search queries,
* preferred source types,
* ticker,
* allowed domains,
* recency limits.

### Process

1. Execute the search queries.
2. Scrape and clean the results.
3. Remove duplicates.
4. Attach metadata.
5. Send the results back to the Researcher Agent.

### Suggested Scraped Record

```json
{
  "url": "...",
  "title": "...",
  "published_at": "...",
  "domain": "...",
  "query_used": "...",
  "article_text": "..."
}
```

### Recommendation

The scraper should remain deterministic and enforce source/recency rules.

---

## Stage 6: Thesis / Synthesis Agent

### Goal

Turn the accumulated evidence into a final stock thesis.

### Inputs

* first-pass research summary,
* follow-up findings,
* unresolved questions,
* evidence quality scores.

### Suggested Output

```json
{
  "ticker": "NVDA",
  "thesis": "Bullish with elevated valuation risk",
  "decision_bias": "buy",
  "confidence": 0.68,
  "key_supporting_facts": ["..."],
  "key_unresolved_risks": ["..."],
  "what_would_change_my_mind": ["..."]
}
```

### Recommendation

This stage should happen before risk management.

---

## Stage 7: Risk Manager

### Goal

Determine whether the thesis is actionable and what size/risk is acceptable.

### Inputs

* thesis,
* evidence confidence,
* unresolved risks,
* source quality,
* volatility,
* portfolio exposure,
* event risk.

### Suggested Output

```json
{
  "ticker": "NVDA",
  "allowed": true,
  "risk_score": 61,
  "max_position_size_pct": 2.5,
  "reasons": [
    "Strong evidence quality",
    "But recent move already large"
  ]
}
```

### Recommendation

Hard limits should still be enforced by deterministic code after this stage.

---

## Stage 8: Final Decision Agent

### Goal

Produce the final buy / sell / hold recommendation.

### Inputs

* final thesis,
* risk output,
* optional portfolio context.

### Output

* action: buy / sell / hold,
* conviction score,
* proposed position size,
* brief explanation.

### Recommendation

The agent should recommend; deterministic execution code should validate and apply the trade.

---

## Suggested Agent Roles

### Industry Analyst Agent

* reads industry-level news,
* scores opportunity,
* identifies key drivers and risks.

### Company Prioritizer Agent

* ranks companies within selected industries,
* explains why they should be researched further.

### Researcher Agent

* summarizes company-specific information,
* extracts evidence,
* identifies bullish and bearish points.

### Questioning Agent

* finds missing information,
* proposes high-value follow-up searches.

### Thesis / Synthesis Agent

* integrates all evidence into a final company view.

### Risk Manager Agent

* decides whether the thesis is safe enough to act on.

### Decision Agent

* outputs buy / sell / hold.

---

## Suggested Research Constraints

To prevent runaway loops:

* max 2 industry candidates forwarded,
* max 3–5 company candidates per industry,
* max 2 follow-up rounds,
* max 2–3 follow-up questions per company per round,
* stop early if evidence quality is already sufficient.

---

## Suggested Evidence Quality Factors

Each claim should be scored based on:

* recency,
* source reputation,
* directness of evidence,
* number of independent confirming sources,
* whether the content is factual reporting or speculation.

---

## Suggested Storage / Logging

Store every step for debugging and evaluation.

### Useful artifacts to store

* industry article bundles,
* industry scores,
* selected industries,
* ticker rankings,
* company article bundles,
* research summaries,
* follow-up questions,
* follow-up results,
* final thesis,
* risk outputs,
* final decisions.

### Recommendation

Use SQLite or Postgres for persistence.

---

## Suggested Folder Structure

```text
trading_app/
  config/
    industries.json
    sources.json
    risk_rules.json

  data/
    scrape_industry_news.py
    scrape_company_news.py
    clean_articles.py
    deduplicate.py
    source_scoring.py

  screening/
    industry_scoring.py
    company_scoring.py
    ticker_mapping.py

  agents/
    industry_analyst.py
    company_prioritizer.py
    researcher.py
    questioning_agent.py
    thesis_agent.py
    risk_manager.py
    decision_agent.py

  orchestration/
    pipeline.py
    round_manager.py
    stopping_rules.py

  execution/
    paper_trading.py
    live_trading.py
    order_validation.py

  storage/
    db.py
    models.py
    logs.py

  docs/
    app_structure.md
```

---

## Best First Version

A practical first version of this app would:

1. scrape news for 10–20 industries,
2. rank industries with the Industry Analyst Agent,
3. keep the top 2 industries,
4. rank 3–5 companies in each,
5. research those companies,
6. ask at most 2 follow-up questions per company,
7. synthesize a thesis,
8. run risk analysis,
9. output buy / sell / hold.

This is enough to build a useful prototype without making the system too open-ended.

---

## Summary

This app should be built as a **controlled multi-stage research pipeline**, not as an unrestricted autonomous swarm.

The strongest version of the system is:

* industry-first,
* evidence-driven,
* multi-round,
* structured,
* risk-aware,
* and debuggable.

The agents should interpret and prioritize.
The code should control and enforce.
