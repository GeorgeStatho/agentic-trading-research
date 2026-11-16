# Project direction: from "LLM picks option trades" to a measured event-drift system

Status: **steps 1–3 implemented, step 4's follower accounts built ahead of its decision point** (see
"Implementation status" below). Figures were read from the live database (read-only) on 2026-11-16 and
will drift.

Paths are relative to the repo root.

## Why change direction

The current idea fails in a specific way, not a general one.

- **The edge is gone by the time the system acts.** News direction predicts the stock when measured
  from the event time (about +0.4% for positive and −1.0% for negative catalysts at one day). Measured
  from when the system observes a candidate, the 5-day direction-adjusted residual in
  `candidate_price_observations` is +0.03% per catalyst (65 catalysts, 50.8% win).
- **Most of the move is the first jump.** On catalysts with all horizons measured (845 positive, 359
  negative), the leg from hour 1 to day 1 is +0.17% and −0.09%, with a 51–53% hit rate.
- **The pipeline trades stale catalysts.** Candidates are created on average 320 hours after the
  catalyst was first seen; 3,077 of 4,010 are created more than a day after. The `FRESHNESS` gate is
  shadow-only, cannot evaluate 4,851 of 5,352 cases (`missing_or_future_event_time`,
  `unavailable_half_life_prior`), and resets a catalyst's age whenever a new article arrives
  (`refreshed_by_new_root_event` in `Python Scripts/services/gates/catalyst_gates.py`).
- **Long at-the-money options do not fit a small, late signal.** Implied volatility averages 0.43 at
  entry and 0.31 at exit; the quoted spread at decision averages 5%. The manager's direction was right
  on the raw 5-day move 92% of the time while real trades win 41%, against a break-even of 55%
  (average loss −36%, average win +30%).
- **The instrument changes the size of a loss, not its sign.** The follower accounts copied five
  production trades (29 October to 9 November) and lost in every instrument, because the stock went
  the wrong way in four of the five.
- **The system changes faster than it can be measured.** 41 trades in six months; 16 pipeline versions
  in eight weeks; a gate promotion needs 100 samples.
- **It is mostly a long bet on large tech.** 1,002 call decisions against 97 puts.

Drift that lasts for days shows up in only two places:

| Family | n | Hour 1 to day 1 | Day 1 to day 5 |
|---|---|---|---|
| Positive earnings | 281 | +0.28% | +0.92% |
| Positive analyst | 139 | +0.54% | +0.49% |
| Negative earnings | 76 | −0.68% | +1.36% (reverses) |
| Positive product/commercial | 144 | +0.04% | −1.01% |
| Macro/sector, either direction | 353 | about 0 | about 0 |

These are subsample cuts from about 45 days in one market regime, with no correction for having
looked at several families. Treat the table as a hypothesis to test.

## Direction

Keep the parts that work: collection, catalyst classification, residual-return measurement, and the
research and shadow tooling. Change what the system is.

| Today | Move to |
|---|---|
| LLMs decide trades | LLMs read articles into structured fields; a simple, fixed rule decides |
| All news, any age | Fresh events only, in families with measured multi-day drift |
| At-the-money options | Shares first; options only as a way to express a proven stock-level edge |
| Judged by option P&L on a few trades | Judged by stock residual from first observation, per distinct catalyst |
| Goal: high win rate | Goal: positive expectancy per catalyst after costs |
| Add gates and stages | Freeze the pipeline while measuring; pre-register the pass mark |

Pause while this runs: learned-model plans 01–03 ([learned_models/00_INDEX.md](learned_models/00_INDEX.md)),
new entry gates, new LLM stages and option exit tuning.

## Steps

Tests first for each, as everywhere in this repo.

### 1. One scoreboard: residual from first observation

- Find out why `candidate_price_observations.forward_residual_return` is filled for only 387 of 5,994
  rows (`capture_forward_returns`, called from
  `Python Scripts/services/discovery/catalyst_response_collector.py:collect_catalyst_responses`), and
  fix the cause so every candidate gets a label.
- Add a read-only `scripts/research.py drift-report`: mean signed residual and hit rate by catalyst
  family, direction and catalyst age at observation (under 1 hour, 1–24 hours, over 24 hours). One row
  per catalyst, with the number of distinct days shown next to every figure.
- This is a report, so it may run on the live database.

### 2. A shares shadow strategy with its own entry and exit

- Add `shares_drift` to `STRATEGIES` in `Python Scripts/services/research/research_contract.py`:
  the direction is the catalyst's direction when the family is earnings or analyst and the catalyst
  was first seen under 24 hours ago, otherwise `skip`. No LLM strategist or manager in the rule.
- Add catalyst age and first-seen time to the features in
  `Python Scripts/services/research/research_inputs.py:load_inputs`, cutoff-checked like the rest.
- Simulate it as stock, entered at observation and held `ResearchPolicy.holding_days` (5), costed at
  `benchmark_cost_bps`, next to the option simulation in `shadow_portfolio.py`.
- It scores every candidate, not only those production traded. The follower accounts cannot answer
  this question, because they copy production's entries and exits (`follow:production`).

### 3. Make freshness mean something

- In `catalyst_gates.py`, record a second shadow verdict: age from `first_seen_time`, with no reset
  from later articles and a fixed 24-hour limit, so it evaluates the cases the current rule cannot.
- Trace why candidates are created days after first sight and record the finding. Fix it only if it
  is a defect and not intended re-evaluation.
- Promotion to a hard gate stays behind a promotion proof
  (`Python Scripts/services/research/research_promotion.py`).

### 4. Decision point (pre-registered)

After `shares_drift` has traded 100 distinct catalysts across at least 20 distinct days, with the
pipeline version held fixed:

- **Positive net residual:** run a `shares` follower with its own exit policy (`STOCK_STOP` already
  exists in `Python Scripts/services/trading/trading_accounts.py`), then test deep in-the-money calls
  as the leveraged version. Use debit spreads only with an exit that holds towards expiry: with
  production's quick exits the spread kept 13% of a move that paid 67% on the single option.
- **Not positive:** the news source is too slow for this signal. The remaining choices are scheduled
  events such as earnings dates ([learned_models/06_scheduled_events.md](learned_models/06_scheduled_events.md)),
  where being late does not matter, or stopping.

## Implementation status (2026-11-16, branch `feat/event-drift-steps-1-3`)

Steps 1–3 are built. Step 4 waits for data.

- **Step 1.** `scripts/research.py drift-report` (`Data/market_db_modules/drift_report.py`). The
  premise was partly wrong: `forward_raw_residual_return` is filled for 5,579 of 6,100 observations.
  The signed column is sparse because the catalyst direction was `unknown` at observation for 5,353
  rows; 1,164 of those were labelled later. The report signs only by the direction known at the time,
  and shows later labels in a separate table. Unfilled rows are not a defect: about 344 have a 5-day
  target after the last stored bar, and 71 (for example SKHY) have under 60 days of history before the
  event. The 424 candidates with no observation are `discovery:` candidates, which never run the
  pricing gate.
  - Each catalyst averaged over all its observations: 65 catalysts, +0.03%, 50.8%. This reproduces
    the figure above.
  - First observation only: 50 catalysts on 11 days, +0.94%, 52%. Later re-observations of a catalyst
    do worse than the first.
- **Step 2.** `shares_drift` in `research_contract.STRATEGIES`, simulated as stock by
  `research_runs.stock_position` on a $1,000 notional (1h bars preferred). It is reported in
  `strategy_table`, but kept out of option pairing and the champion hurdle (`UNPAIRED_STRATEGIES`).
  Each trade also records `residual_return` and `residual_pnl`: the same position net of SPY and the
  sector, measured as the drift report measures it. Use `scripts/research.py shares-drift-report <run_id>`
  for step 4. It counts each catalyst once, at its first trade, as the live followers enter, and gives raw
  and residual returns side by side.
  - The first backtest (46 trades on 23 catalysts over 6 days, −1.5% raw, 39% win) was raw price
    return, counted every repeat candidate, and mostly reflects the market on those days. It is replaced
    by the figure below.
  - Rerun on a snapshot of all 6,100 candidates (2026-11-16), one row per catalyst: 23 catalysts on 6
    days. Raw −2.05% (35% hit), residual +0.04% (39% hit), with the residual measured on all 23.
    Earnings-positive alone: 21 catalysts, residual +0.02%. So the raw loss was the market. Net of it, the
    strategy shows nothing yet either way, on a sample far below step 4's bar.
- **Step 3.** A shadow `FRESHNESS_FIRST_SEEN` verdict (`catalyst-gates-v4`). On 6,393 stored candidate
  catalysts, it decides the 3,070 cases the old rule could not (`unavailable_half_life_prior`): 692
  fresh, 2,378 stale. It also marks 356 as stale that the old rule called fresh only because a later
  article reset their age.
- **Late creation trace.** Of about 3,077 candidates created more than 24h after first sight, only 144
  are the first candidate for their catalyst. The rest are re-evaluations of a catalyst that already
  had a candidate, which is intended behaviour. Those 144 arrive about 5 days late on average; their
  cause is not traced yet.
- **Step 4, built early on request (before the 100-catalyst bar).** Follower accounts can enter on the
  `shares_drift` rule, once per catalyst, instead of copying production (`ACCOUNT_<NAME>_ENTRY=shares_drift`).
  They then exit by their own policy: `hold_window` (5 calendar days, no stop, the same as the research
  strategy) for shares and deep in-the-money calls, and `hold_to_expiry` for debit spreads. ITM calls now
  need delta 0.80 or more, at the first expiry at least 14 days out. Replaying the entry rule over a
  snapshot to 2026-11-12 gives 26 catalysts on 8 days. The live results are evidence for, not a substitute
  for, the pre-registered decision.
  - The ITM and expiry rule also applies to the existing `itm` account that copies production. Its trades
    after this deploy are not comparable with its first five.
  - Live entries wait for the market to open and skip candidates more than 24 hours old. The research
    strategy enters at the first completed bar after the cutoff, so an overnight candidate enters at the
    open live but at the 10:30 hourly close in research.
- **Sample rate.** About 25 distinct fresh earnings/analyst catalysts with a known direction in 8
  weeks, so step 4's bar (100 catalysts on 20 days) is several months away. Missing directions are not
  the cause: only 6 of 63 fresh earnings or analyst candidate rows had an unknown direction. The
  shortage is fresh catalysts in those families.

## Verification, when the steps are implemented

- `python3 scripts/project.py test` for the new tests, and `python3 -m ruff check .`.
- `drift-report` on the live database reproduces the +0.03% after-observation figure before any
  restriction is applied.
- `shares_drift` appears in the research comparison reports with `complete` rows, and all its entries
  are under 24 hours from first sight (asserted in a test and spot-checked in the report).
- No worker, collector or `forward-cycle` run from the main checkout.

## Open points

- Whether catalyst direction is classified with later coverage in view, which would inflate the
  event-anchored figures.
- The units of `option_trade_executions.spread_paid_pct`, and so how close to the mid paper fills land.
- Resolved 2026-11-16: both put trades failed to enter in the `shares` follower account because the
  Alpaca paper account had no shorting permission. That has been granted, so the `shares_drift` shorts
  (5 of the 26 replayed signals) can enter.
- "Fresh" is measured from first sight, not publication. Of the 26 replayed signals, 20 were public
  1–24 hours before the system saw them and 2 for days. A publication-time limit would be stricter.
- The doc's own table shows negative earnings reversing over days 1–5, and the evidence supports only the
  positive side. The rule still trades both directions, as the plan text says; that is a choice to revisit.
