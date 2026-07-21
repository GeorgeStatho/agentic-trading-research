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
- Article, entity, and current paper-position nodes show their stored metadata. Edge thickness is driven by deterministic exposure score; edge labels contain the grounded causal mechanism.
- The “Why this matters” drawer shows source links, uncertainties, deterministic inputs, and a clear **No trade is executed by this feature** banner.
- Exposure analysis is cached and persisted, making demos and later reviews reproducible.
- Linked non-paper executions are shown separately as historical outcome evidence. They are never treated as open exposure or candidates for trading; they let a reviewer see which live/past trade outcomes were affected and what the system later learned.

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

### 10. Decision Expectancy Dashboard

Added `#/decision-expectancy`, a read-only cohort-analysis page for answering: **Where has the system historically performed well or poorly?**

- Groups stored manager decisions by sector, decision direction, confidence, DTE bucket, selector mode, and time of day.
- Supports both marked P/L and closed/realized P/L views, with a clearly labelled freshness boundary for marked outcomes.
- Reports trade count, usable P/L sample count, win rate, average P/L (the canonical expectancy), and sample-size warnings.
- Includes filters for date window, confidence, decision type, sector, grouping, and minimum sample count, so sparse cohorts are not mistaken for robust findings.
- Uses stored decision and outcome data only. It never calls an LLM, fetches live market data, changes a decision, or submits an order.

### 11. Historical Decision Backtesting

Added `#/backtests`, an immutable historical simulation workspace that evaluates stored manager decisions against persisted historical option bid/ask quotes.

- Each run freezes its decision inputs and configuration, persists an event ledger, and can be reopened later for audit.
- Entries fill only at a valid next ask; exits use a later valid bid. Missing, stale, or invalid quotes become explicit `no fill` or coverage events rather than invented prices.
- Every event records decision time, evidence snapshot, selected contract, quote provider/source, entry and exit values, P/L, commissions, slippage, warnings, and exit reason.
- The dashboard exposes entry/exit slippage, commission per contract, take-profit, stop-loss, and maximum-hold controls. Positions close at the first valid bid that reaches the configured profit or loss threshold, otherwise at the final valid bid inside the hold window.
- The run summary shows fill rate, no-fill coverage gaps, expectancy, win rate, total P/L, drawdown, cumulative P/L, and the complete evidence-to-outcome event ledger.

### 12. Historical Option-Quote Coverage

Historical quotes are stored in `historical_option_quotes` and are independent from live trading data.

- Added an idempotent Alpaca-format CSV importer for users who already have a compatible export.
- Replaced the unsupported Alpaca historical-options fetch path with a `MARKETDATA_TOKEN`-backed MarketData.app EOD quote fetcher.
- The dashboard can request EOD quote coverage server-side; the token stays on the server and never reaches the browser.
- Fetching handles paged/parallel quote responses, records provider and raw response provenance, skips cached quotes, and writes unavailable contracts or dates as auditable coverage gaps.
- The resulting quote rows feed the backtest only; no quote-import or quote-fetch action can place an order.

### 13. Portfolio-Level Risk Controls

The same deterministic portfolio constraints now apply to both historical backtests and the real/paper option-entry executor.

- **Maximum simultaneous positions:** reject a new symbol once the configured open-position count is reached.
- **Total option entry notional:** reject entries that would exceed the configured deployable option buying-power cap.
- **Sector concentration:** resolve the company’s persisted sector and reject entries that would exceed the configured sector allocation cap.
- **Daily realized-loss limit:** reject new entries after broker-confirmed, persisted losses reach the configured daily limit.
- Rejections are explicit audit outcomes (`portfolio_max_concurrent_positions`, `portfolio_total_entry_notional_limit`, `portfolio_sector_notional_limit`, or `portfolio_daily_loss_limit`) and occur before any broker order-submission call.

For live/paper execution, the session reserves current broker option exposure before considering a new order, then updates its in-memory exposure state only after a successful submission. Existing-position monitoring and exits are not blocked by these entry controls.

### 14. Seeded Demo Mode

Added a deterministic `Demo Mode` for a reliable hackathon walkthrough even when live data, open positions, or model credentials are unavailable.

- A seeded fictional Uber scenario creates an article, negative impact path, short-DTE position risk explanation, and matching autopsy review.
- The demo explicitly identifies itself as cached fictional data and does not create live news, positions, orders, trades, or model calls.
- It demonstrates the intended review flow: evidence → exposure path → deterministic risk → explanation → post-trade accountability.

### 15. Evidence-Confirmed Trend Continuation Gate

Added a versioned, deterministic daily swing-continuation gate between the Manager recommendation and contract selection. The Manager may propose a call or put, but the gate—not the model—decides whether it is eligible to become an order.

- Requires grounded article IDs, at least two qualifying evidence items from independent sources, and a minimum stored evidence score.
- Calls require bullish daily trend, stock relative-strength confirmation, a sector ETF outperforming SPY, strong relative-volume confirmation, controlled overextension, and good/excellent call-entry quality. Puts apply the symmetric bearish rules.
- Uses an explicit `sector_market_relative_strength` metric (sector ETF 20-day return versus SPY), distinct from the existing stock-versus-sector comparison.
- Fails closed when required evidence or daily technical data is missing. It intentionally labels daily-bar VWAP as daily—not intraday—and does not claim intraday confirmation.
- Preserves `proposed_decision`, `eligible_decision`, `reason_codes`, evidence counts/scores, technical state, policy version, and enforcement mode. The original Manager recommendation is never overwritten.
- Can run in `shadow` mode to record what it would have blocked before enforcement. `enforce` mode blocks both batch and immediate-streaming order candidates unless `entry_gate.allowed` is true.
- Contract liquidity remains a separate final check. A confirmed continuation with no acceptable contract is recorded as `continuation_confirmed_but_no_eligible_contract` rather than a generic no-trade.

### 16. Deterministic Market-Regime Gate

Added a separate, market-wide execution safeguard that classifies the trading environment once per cycle and applies a non-overridable policy after evidence continuation and option selection.

- Builds one normalized snapshot from SPY daily history, realized volatility, VIX percentile when available, data freshness, and the persisted macro calendar. The exact same snapshot is supplied to every Strategist and Manager evaluated in that cycle.
- Uses strict deterministic priority: `stressed` → `high_volatility` → `trend_up` / `trend_down` → `range` → `unknown`. Missing or stale market/calendar inputs fail closed as `unknown`.
- Normalizes macro schedule fields (`scheduled_at_utc`, `calendar_timezone`, and `calendar_refreshed_at`) and blocks new directional entries around high-impact US events such as CPI, PCE, payrolls, GDP, and FOMC decisions. Unknown same-day event times are explicitly recorded as high risk rather than guessed.
- Enforces conservative V1 policy: high-confidence aligned calls in `trend_up` and puts in `trend_down`; high-confidence-only in `high_volatility`; no fresh directional entries in `stressed`, `range`, macro blackout, or `unknown` states.
- Supports `observe`, `shadow`, and `enforce` rollout modes. The gate records `proposed_decision`, `eligible_decision`, deterministic zero-or-one sizing, reason codes, regime state, macro window, policy version, and enforcement mode. It never claims fractional sizing when the minimum order is one contract.
- Persists each snapshot in `market_regime_runs`, retains it in manager input/history and the immutable autopsy, exposes current state and freshness on Overview, and enables expectancy cohorts by regime.
- Requires `market_regime_gate.allowed` at both batch and immediate-streaming order-candidate paths, so neither execution path can bypass the policy. Existing-position monitoring and exits continue independently.

## Architecture

```text
SQLite news + market data
  ├─ persisted articles / impacts / relationships
  ├─ strategist runs
  ├─ manager decision history
  ├─ immutable decision autopsy ledger
  ├─ paper and linked live trade executions / outcomes
  ├─ historical option quotes + quote-fetch coverage gaps
  ├─ immutable backtest runs + event ledger
  ├─ persisted market-regime snapshots + macro calendar timestamps
  └─ decision-expectancy cohorts
         ↓
Flask read APIs
         ↓
React dashboard
  ├─ Overview
  ├─ Company News
  ├─ Decisions
  ├─ Exposure Map
  ├─ Decision Autopsy Ledger
  ├─ Decision Expectancy
  └─ Historical Backtests
```

## GPT-5.6 and Codex Usage

- The Exposure Map is designed for structured, grounded analysis with OpenAI’s configured GPT-5.6 model path (`gpt-5.6-terra` by default for `LLM_PROVIDER=openai`).
- GPT-generated content is limited to explanations and structured assessment fields; deterministic Python validates identifiers and computes risk.
- Codex was used to implement the Flask APIs, SQLite persistence, provider configuration, React Flow graph, dashboard UX, test coverage, and audit/feedback controls across the codebase.

## Safety and Auditability

- New research, exposure, autopsy, expectancy, and backtest features are read-only and never place an order.
- Exposure Map calculates current exposure only from paper positions; non-paper executions appear only as historical outcome evidence.
- Model references are validated against the exact supplied payload.
- Decision snapshots are immutable; outcomes are joined separately for review.
- Manager feedback is limited to resolved historical outcomes and cannot override current evidence or deterministic risk logic.
- Backtests use stored historical bid/ask quotes only. Missing data remains visible as a no-fill or coverage-gap event rather than becoming a fabricated price.
- Live/paper entry constraints are deterministic, configured server-side, and checked before an order-submission call.
- Evidence-continuation eligibility is separately enforced before deterministic contract selection and again at order-candidate construction for both batch and streaming execution paths.
- Market-regime classification is deterministic, persisted, and separate from the LLM. The Manager can explain a thesis in regime context but cannot override the gate's execution policy.
- Stale/missing market or macro-calendar data fails closed for new directional entries; macro blackout blocks new entries without preventing existing-position risk management.
- MarketData.app credentials stay server-side; the dashboard never receives the provider token.

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
10. Return to **Overview** to show the current deterministic market regime, freshness clock, and active/upcoming macro blackout context.
11. Open **Decisions** to inspect the continuation and market-regime permissions/reason codes retained alongside each Manager proposal.
12. Open **Decision Expectancy** to compare confidence, sector, DTE, continuation-gate, and market-regime cohorts using stored outcomes only.
13. Open **Backtests**, fetch available EOD quote coverage, then run an immutable simulation and inspect its no-fill/coverage records, exits, P/L, and portfolio-limit rejections.

## Validation Performed

Focused automated tests cover:

- Exposure model selection, invalid JSON, unknown references, no-position analysis, risk-score calculation, and queue freshness cutoff.
- Strategist grounding, invalid citations, persisted runs, and manager handoff.
- Immutable manager-decision ledger snapshots and resolved-outcome filtering.
- Manager payload/prompt contract for resolved autopsy feedback.
- Executed-order 30-day cutoff.
- Decision-expectancy filter validation, cohort math, marked-versus-closed outcome handling, and reader queries.
- Historical bid/ask fill selection, stale-quote rejection, configurable exits, drawdown calculations, immutable run persistence, and portfolio-level simulation limits.
- Alpaca-compatible CSV import idempotency, MarketData.app EOD quote normalization, missing-token coverage gaps, and the server-side EOD-fetch API.
- Live/paper executor portfolio guardrails, including verification that every rejection happens before broker order submission.
- Evidence-continuation approvals, weak volume, sector underperformance, missing technical data, overextension/chase risk, symmetric put confirmation, shadow mode, no-contract outcomes, and batch/streaming candidate blocking.
- Market-regime trend classification, stale-data fail-closed behavior, high-volatility confidence limits, stressed and macro-blackout overrides, shadow mode, cycle-level snapshot reuse, persisted snapshots, and batch/streaming candidate blocking.
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

# Live/paper deterministic option-entry constraints
MAX_CONCURRENT_OPTION_POSITIONS=5
MAX_OPTION_SECTOR_EXPOSURE_PCT=50
DAILY_OPTION_LOSS_LIMIT_DOLLARS=2000

# Versioned daily continuation gate: use shadow before enforce.
EVIDENCE_CONTINUATION_MODE=shadow

# Market-wide deterministic execution policy: observe/shadow before paper enforce.
MARKET_REGIME_GATE_MODE=shadow
# MARKET_REGIME_MAX_MARKET_DATA_AGE_SECONDS=259200
# MARKET_REGIME_MAX_MACRO_CALENDAR_AGE_MINUTES=1440

# Server-side historical EOD option quote coverage
MARKETDATA_TOKEN=...
```

```bash
# Offline A/B replay; this never executes or places a paper trade.
python3 "Python Scripts/agentCallers/offline_manager_evaluation.py" --limit 100

# Historical calibration audit for legacy decisions; does not invoke an LLM.
python3 "Python Scripts/agentCallers/offline_manager_evaluation.py" --historical-outcome-audit --limit 100

# Approximate legacy replay from saved decision-time snapshot fields only.
# Keep this report separate from exact frozen-input replay metrics.
python3 "Python Scripts/agentCallers/offline_manager_evaluation.py" --legacy-partial-replay --limit 100

# Fetch stored-decision option quotes from MarketData.app without exposing the token to a browser.
python3 "Python Scripts/agentCallers/fetch_marketdata_eod_option_quotes.py" --limit 100
```

## Submission Notes

- Include a short demo video showing the end-to-end flow above.
- Include setup instructions and sample/demo data in the project README.
- In the submission, call out this document, the model/provider configuration, and the Codex session used for the core implementation.
