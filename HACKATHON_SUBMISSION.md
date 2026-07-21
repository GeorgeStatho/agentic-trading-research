# Stock Trading Experiment — Hackathon Additions

## Project Summary

Stock Trading Experiment is an evidence-first, paper-trading research dashboard. This hackathon work turns the existing news, strategist, manager, execution, and position data into an auditable decision workflow rather than a black-box prediction product.

The project fits the **Apps for your life** track as a personal-finance research and accountability tool. It does not place trades from the new features described below.

## What We Built

### 1. Evidence Exposure Map

Added `#/exposure-map`, an interactive `@xyflow/react` graph that starts from one news article and shows the grounded path to affected entities and current paper-option positions:

```text
News article
  → sector / industry / company relationship
    → current paper option position
      → deterministic risk score + grounded model explanation
```

Key behavior:

- The Evidence Queue lists only articles published in the last `DEFAULT_MAX_ARTICLE_AGE_DAYS * 2`, sorted by evidence score and then recency.
- The graph has pan, zoom, fit-to-view, node inspection, direction filters, position-risk filters, and a legend for evidence/edge meaning.
- Article, entity, and paper-position nodes show their stored metadata. Edge thickness is driven by deterministic exposure score; edge labels contain the grounded causal mechanism.
- The “Why this matters” drawer shows source links, uncertainties, deterministic inputs, and a clear **No trade is executed by this feature** banner.
- Exposure analysis is cached and persisted, making demos and later reviews reproducible.

### 2. Grounded Exposure Analysis

The exposure model receives only the selected article and persisted context:

- Stored article metadata and evidence score.
- Existing sector, industry, and company relationships.
- Current open **paper** positions and option metadata: DTE, strike, side, quantity, and implied volatility when available.

The model returns structured JSON for article-to-entity edges and position impacts. Backend validation rejects entity IDs, article IDs, and option symbols that were not in the supplied context.

The deterministic risk score is calculated in Python, not by the model:

```text
position exposure × impact magnitude × confidence weight × DTE/volatility multiplier
```

Direction and option-side alignment increase risk for adverse exposure. This separates the model’s job—explain a grounded relationship—from code’s job—quantify exposure.

### 3. Provider-Agnostic Model Support

The LLM provider abstraction now supports the configured provider rather than tying Exposure Map to one model:

- `LLM_PROVIDER=openai` defaults Exposure Map to `gpt-5.6-terra`.
- Vertex and Ollama use their configured exposure/research models.
- `EXPOSURE_MAP_MODEL` can explicitly override the default.
- The UI can send a per-analysis model override for demos.

Every exposure run stores model name, prompt version, input article IDs, output, status, and timestamp.

### 4. Grounded Strategist Runs

The Strategist is now held to the same evidence standard as Exposure Map:

- It receives persisted article IDs, company/industry/sector relationships, and dated market context.
- Its structured output includes grounded company/entity identifiers and per-claim article citations.
- Validation rejects any company, relationship, or article reference outside its input payload.
- Every strategist run persists model, prompt version, input article IDs, complete input, output, status, and timestamp for replay.

The Manager still evaluates current market, account, option, and deterministic risk constraints independently; it does not blindly accept the Strategist thesis.

### 5. Decision Autopsy / AI Accountability Ledger

Added `#/decision-autopsy`, a read-only ledger that answers: **What did the system know, decide, constrain, and learn?**

For every manager decision, the project records an immutable snapshot of:

- Grounded strategist thesis, confidence, cited evidence, and stated risks.
- Manager decision, confidence, reason, target DTE, selected contract metadata, and deterministic selection context.
- Compact article references and available market/account context at decision time.

The ledger joins only live outcome fields—execution status, order ID, eligible P/L, and outcome label—without changing the stored decision snapshot. It also provides a deterministic post-trade review:

- No execution: marks the proposal as evidence-only.
- Pending outcome: clearly states that review is not yet complete.
- Adverse P/L: surfaces the first preserved pre-trade risk as the recorded invalidation signal.
- Favorable P/L: preserves risks as still-live constraints rather than declaring the thesis permanently correct.

Legacy manager-history rows are backfilled into the ledger once, so earlier decisions remain reviewable.

### 6. Outcome-Aware Manager Feedback

The Manager now receives a compact set of resolved, same-company autopsy insights:

- Prior manager and strategist decisions, confidence, DTE bucket, and referenced article IDs.
- Eligible outcome P/L and outcome label.
- Preserved pre-trade risks and a deterministic “what proved us wrong” summary.

This is calibration feedback, not fresh evidence. The prompt explicitly prevents it from being cited as support for a new thesis or overriding current grounded research, option-pricing checks, position limits, liquidity checks, or deterministic trade gates. The feedback supplied to a new manager decision is saved in that decision’s audit record.

### 7. Dashboard UX Refresh

Updated all dashboard surfaces for faster review:

- **Overview:** grouped account snapshot, performance/safeguards, research rankings, framed equity chart, consistent order history, risk, script-status, and position panels.
- **Company News:** clearer active-layer metrics, per-view counts, live-refresh state, resettable confidence/search filters, and more scannable evidence cards.
- **Decisions:** decision metrics, manager-decision filters, strategist/manager badges, and collapsible replay history.
- **Exposure Map:** internal sidebar scrolling, filters, legend, graph controls, selected-node emphasis, and responsive layouts.

### 8. Query Hygiene and Retention

- Company News and Exposure Queue use a `DEFAULT_MAX_ARTICLE_AGE_DAYS * 2` freshness window where appropriate.
- Article-link and impact-table indexes support common joins.
- Executed Orders display only the last 30 days, while older executions remain stored in SQLite for historical review.

### 9. Offline Manager Evaluation

`offline_manager_evaluation.py` replays resolved, historically executed manager decisions twice: once with no autopsy feedback and once with only same-company autopsies whose outcome was known before the historical decision time. It records both recommendations and reports win rate, average P/L, false-positive rate, and confidence-bucket outcomes.

It does not refresh live market data, select an option contract, submit an order, or modify paper-trading execution records. Alternate directions are marked counterfactual and remain unscored until historical option-price simulation is added.

## Architecture

```text
SQLite news + market data
  ├─ persisted articles / impacts / relationships
  ├─ strategist runs
  ├─ manager decision history
  ├─ immutable decision autopsy ledger
  └─ paper trade executions and outcomes
         ↓
Flask read APIs
         ↓
React dashboard
  ├─ Overview
  ├─ Company News
  ├─ Decisions
  ├─ Exposure Map
  └─ Decision Autopsy Ledger
```

## GPT-5.6 and Codex Usage

- The Exposure Map is designed for structured, grounded analysis with OpenAI’s configured GPT-5.6 model path (`gpt-5.6-terra` by default for `LLM_PROVIDER=openai`).
- GPT-generated content is limited to explanations and structured assessment fields; deterministic Python validates identifiers and computes risk.
- Codex was used to implement the Flask APIs, SQLite persistence, provider configuration, React Flow graph, dashboard UX, test coverage, and audit/feedback controls across the codebase.

## Safety and Auditability

- New research, exposure, and autopsy features are read-only and never place an order.
- Exposure Map only considers current paper positions.
- Model references are validated against the exact supplied payload.
- Decision snapshots are immutable; outcomes are joined separately for review.
- Manager feedback is limited to resolved historical outcomes and cannot override current evidence or deterministic risk logic.

## Demo Flow

1. Select **Demo Mode** in the dashboard header for the always-available seeded Uber walkthrough.
2. Open **Exposure Map** to show a source article, red sector/company/short-DTE option path, causal explanation, and deterministic risk inputs.
3. Open **Autopsy Ledger** while Demo Mode remains active to show the matching evidence and “what proved us wrong?” review.
4. Exit Demo Mode to show live project data, then open **Company News** and select a recent high-evidence article.
5. Open **Exposure Map**, choose that article, and run/cached-run grounded analysis.
6. Filter to negative impact or high-risk position paths; select a node to inspect source, causal mechanism, score inputs, and uncertainty.
7. Open **Decisions** to compare Strategist and Manager output.
8. Open **Decision Autopsy Ledger** to replay the proposal, evidence, constraints, execution outcome, and post-trade review.
9. Show that a future manager run receives only compact, resolved autopsy feedback while preserving current evidence and deterministic safety gates.

## Validation Performed

Focused automated tests cover:

- Exposure model selection, invalid JSON, unknown references, no-position analysis, risk-score calculation, and queue freshness cutoff.
- Strategist grounding, invalid citations, persisted runs, and manager handoff.
- Immutable manager-decision ledger snapshots and resolved-outcome filtering.
- Manager payload/prompt contract for resolved autopsy feedback.
- Executed-order 30-day cutoff.
- Dashboard TypeScript/Vite production builds.

## Important Configuration

```bash
# OpenAI-backed exposure analysis
LLM_PROVIDER=openai
OPENAI_API_KEY=...
EXPOSURE_MAP_MODEL=gpt-5.6-terra  # optional explicit override

# Outcome-feedback bounds for the Manager
MANAGER_RESOLVED_AUTOPSY_LIMIT=3
MANAGER_RESOLVED_AUTOPSY_MAX_AGE_DAYS=180
```

```bash
# Offline A/B replay; this never executes or places a paper trade.
python3 "Python Scripts/agentCallers/offline_manager_evaluation.py" --limit 100

# Historical calibration audit for legacy decisions; does not invoke an LLM.
python3 "Python Scripts/agentCallers/offline_manager_evaluation.py" --historical-outcome-audit --limit 100

# Approximate legacy replay from saved decision-time snapshot fields only.
# Keep this report separate from exact frozen-input replay metrics.
python3 "Python Scripts/agentCallers/offline_manager_evaluation.py" --legacy-partial-replay --limit 100
```

## Submission Notes

- Include a short demo video showing the end-to-end flow above.
- Include setup instructions and sample/demo data in the project README.
- In the submission, call out this document, the model/provider configuration, and the Codex session used for the core implementation.
