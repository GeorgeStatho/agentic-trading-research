# Research and decision pipeline: operator guide

How the research pipeline (Phases 0–7) fits together, what runs where, how to turn each part on,
and how to watch it. The phase documents hold the details; this is the map.

Everything here is **shadow by default**: it records what it would do. Nothing in Phases 1–7 places
an order. The only way a research result can change live trading is an explicit promotion, and
promotion needs recorded evidence that matches the current code and settings.

## The pipeline in one picture

```text
news + SEC filings ──▶ evidence packages (P1) ──▶ discovery queue (P2) ──┬─▶ research sessions (P3) ──▶ research theses (P4)
                                                                         └─▶ position reviews (P6)
production cycle: strategist + evidence thesis (P4) ──▶ manager ──▶ selector + gates ──▶ trade economics (P5) ──▶ order
                                                         └─▶ decision observation ──▶ challenger evaluation (P7)
every decision ──▶ forward cohorts + closing marks ──▶ comparison reports ──▶ (explicit) promotion (P7)
```

| Phase | What it adds | Read |
| --- | --- | --- |
| 0 | Shared observations, evaluator identity, immutable research records | [Phase 0](RESEARCH_PHASE0.md) |
| 1 | SEC filings, XBRL facts and issuer releases as dated evidence packages | [Phase 1](RESEARCH_PHASE1.md) |
| 2 | Event discovery for companies of interest and a durable, leased queue | [Phase 2](RESEARCH_PHASE2.md) |
| 3 | A bounded research loop: a model asks for archive lookups, Python runs them | [Phase 3](RESEARCH_PHASE3.md) |
| 4 | A falsifiable `evidence_thesis` with every claim checked against its sources | [Phase 4](RESEARCH_PHASE4.md) |
| 5 | Black-Scholes trade economics on past catalyst outcomes, rechecked at submission | [Phase 5](RESEARCH_PHASE5.md) |
| 6 | Thesis reviews of open positions when a material event arrives | [Phase 6](RESEARCH_PHASE6.md) |
| 7 | Rolling forward cohorts, challenger models, pre-registered promotion | [Phase 7](RESEARCH_PHASE7.md) |

## What runs where

| Process (container) | Research work it does | When |
| --- | --- | --- |
| `news_collector` | Price responses and forward labels, Phase 7 challenger evaluations and reports, SEC market-wide filings (`market_event` interest), Phase 1 SEC collection, Phase 2 discovery scan, research (P3) and research theses (P4), and news for discovery companies outside the ranking | Every collection cycle (`NEWS_COLLECTION_INTERVAL_SECONDS`, 1 h in the template) |
| `worker` | Opens production cohorts (P7). Before the strategist/manager: discovery scan and admission (P2, no model calls); with `DISCOVERY_MODE=enforce`, admitted events join the strategist and manager. During decisions: evidence thesis (P4), economics (P5), decision observations (P7). | Every trading cycle |
| `option_manager` | Event thesis reviews (P6), closing marks for research positions (P7) | Each management cycle; marks once per session ~10 min before the close |
| `api` | Read-only `/api/v2-status` and `/api/forward-evaluation` | On request |

Schema changes apply on the first start of any service after a deploy. Migrating a copy of the live
database took about 3 s.

## Defaults and dependencies

| Setting | Template default | Needs | Model cost |
| --- | --- | --- | --- |
| `COMPANY_EVIDENCE_ENABLED` | `false` | `SEC_USER_AGENT` with an `@` contact | none |
| `DISCOVERY_MODE` | `disabled` (or `shadow`, `enforce`) | Phase 1 for primary evidence; otherwise news only | none in the queue; in `shadow` and `enforce`, news classification for each admitted company outside the ranking |
| `DISCOVERY_MARKET_EVENT_LIMIT` | `0` (off) | `SEC_USER_AGENT`; companies above the price and volume floors | none |
| `RESEARCH_MODE` | `disabled` | `DISCOVERY_MODE=shadow`, a planner model and, on a billed provider, a cost budget and rates | planner calls |
| `EVIDENCE_THESIS_MODE` | `shadow` | Nothing on the production route; the research route needs `RESEARCH_MODE=shadow` | inside the strategist call; research theses add calls |
| `TRADE_ECONOMICS_MODE` | `shadow` | Price-response history from the collector for scenarios | none |
| `ENTRY_EXTENSION_MODE` | `shadow` | The manager's `overextension_risk` and the payload's 5-day return; `enforce` needs an `ENTRY_EXTENSION` proof | none |
| `TRADABILITY_MODE` | `shadow` | Alpaca market data; `enforce` needs a `TRADABILITY` proof and still skips no model call | none; about 5 Alpaca requests a candidate and 1 account read a cycle (`disabled` makes none) |
| `EVENT_THESIS_REVIEW_MODE` | `shadow` | `DISCOVERY_MODE=shadow` (it creates the review items) | 3 adjudicator calls a review |
| `FORWARD_EVALUATION_MODE` | `shadow` | `option_manager` for closing marks | none |
| `RESEARCH_CHALLENGER_MODE` | `off` | A challenger model | up to 2 calls a production decision |

**Phase 3 cannot reach `ready` without Phase 1.** On the live data with company evidence off, every
research session asked the planner once and then deferred with `verified_company_facts_unavailable`.

## Turning it on

For the step-by-step server checklist (backup, pull, rebuild, verify), see
[RESEARCH_LIVE_SETUP.md](RESEARCH_LIVE_SETUP.md).

Enable in this order. Each step feeds the next, and each can run for a while on its own.

1. **Evidence (P1).**

   ```dotenv
   COMPANY_EVIDENCE_ENABLED=true
   SEC_USER_AGENT=StockExperiment you@example-mail.com
   ```

   Use your real application name and an email you read. SEC requires the contact; without an
   `@` the collector returns `configuration_required`. Check progress with
   `docker compose exec news_collector python scripts/company_evidence.py collect` or the collector output.

2. **Discovery (P2), which also switches on event reviews (P6).**

   ```dotenv
   DISCOVERY_MODE=shadow
   ```

   - Discovery needs no model.
   - Check it with `docker compose exec news_collector python scripts/discovery.py status`, then
     `interest` and `checks`.
   - The first scan queues every current catalyst for each company of interest, including
     position reviews for open positions. Most of the first reviews are skipped as pre-entry
     evidence. Reviews beyond three per position per day wait for the next New York day and are
     reviewed then.

3. **Research (P3/P4 research route).** Only after Phase 1 has collected facts.

   ```dotenv
   RESEARCH_MODE=shadow
   RESEARCH_PLANNER_MODEL=<model>
   # On Vertex or openai (or a paid openai_compatible/gemini plan) also set RESEARCH_DAILY_COST_BUDGET
   # and the per-million-token rates.
   ```

   It runs in the news collector after discovery, so it never delays a trading decision. A
   session for a company without verified facts defers before any planner call.

4. **Market-wide filings, optional.**

   ```dotenv
   DISCOVERY_MARKET_EVENT_LIMIT=20   # companies a cycle; 0 turns it off
   ```

   Each cycle reads SEC's current-filings feed and gives up to that many filers above
   `DISCOVERY_MARKET_EVENT_MIN_PRICE` and `DISCOVERY_MARKET_EVENT_MIN_AVERAGE_VOLUME` a
   `market_event` interest, so discovery sees companies outside the ranking.

5. **Discovery into production (`enforce`), only after the ledger.** Wait until
   `research.py discovery-ledger` reports `promotion_check.passed: true`
   ([Phase 7](RESEARCH_PHASE7.md)), then set `DISCOVERY_MODE=enforce`. Each cycle, up to
   `DISCOVERY_PRODUCTION_LIMIT` (5) admitted companies join the strategist and manager, carrying
   their event as the primary catalyst. An event is routed once, only after the collector has
   refreshed that company's news, and only while it is fresh: the catalyst's decay rule, or
   `DISCOVERY_ROUTE_MAX_AGE_SECONDS` (2 days) for catalysts without one. This is the one setting
   here that changes trading.

6. **Challenger (P7), optional.**

   ```dotenv
   RESEARCH_CHALLENGER_MODE=shadow
   RESEARCH_CHALLENGER_MANAGER_MODEL=<model>   # and/or _STRATEGIST_MODEL
   ```

   Keep `RESEARCH_CHALLENGER_DAILY_CALLS` near two per production decision (about 120 today), or
   the backlog counts against coverage.

`EVIDENCE_THESIS_MODE`, `TRADE_ECONOMICS_MODE`, `EVENT_THESIS_REVIEW_MODE`, `ENTRY_EXTENSION_MODE`,
`TRADABILITY_MODE` and `FORWARD_EVALUATION_MODE` are already `shadow` and need nothing. `TRADABILITY`'s metrics
(each side's top pick, its premium, the cap, the bucket, any failure) are what to compare against later
`EXECUTION premium_per_trade_cap` and `continuation_confirmed_but_no_eligible_contract` blocks. The `REGIME_SPY_TREND` regime variant
has no mode: it is always shadow and never changes the `REGIME` verdict.

## Watching it

Run these from the repository directory on the server. They run inside the containers, which hold
the import paths and the database volume; plain `python3 scripts/...` on the host fails with
`ModuleNotFoundError`. All commands are read-only unless noted. Pass `--db /path/to/db` to read a
copy instead of the configured database; the path is inside the container.

| Question | Command |
| --- | --- |
| Is evidence being collected? | `docker compose exec news_collector python scripts/company_evidence.py collect` (runs one cycle) |
| What is discovery holding? | `docker compose exec news_collector python scripts/discovery.py status` / `interest` / `checks` |
| What did research find, and why did it stop? | `docker compose exec worker python scripts/research.py replay 'research:…'` |
| Are theses checkable? | `docker compose exec worker python scripts/research.py thesis-report --days 7` |
| What would economics have blocked? | `docker compose exec worker python scripts/research.py economics-report --days 7` |
| What did event reviews conclude? | `docker compose exec worker python scripts/research.py event-reviews --days 7` |
| Is forward evidence accumulating? | `docker compose exec worker python scripts/research.py forward-status` |
| How does the challenger compare? | `docker compose exec worker python scripts/research.py compare COHORT_ID` (writes a report) |
| Which route admits better events, and may discovery enforce? | `docker compose exec worker python scripts/research.py discovery-ledger --days 30` (see `promotion_check`) |
| Which gate or tag separates winners from losers (incl. unknown rate)? | `docker compose exec worker python scripts/research.py gate-ledger --days 60` (5-day residual move signed by side; gate value as in the promotion proof, losses avoided minus profits sacrificed; per regime and manager model; slow on a large database) |
| Do research theses hold up? | `docker compose exec worker python scripts/research.py research-outcomes --days 30` |
| Which challenger is worth a cohort? | `docker compose exec worker python scripts/research.py benchmark run SET_ID --model M --split dev`, then `benchmark compare` (screening only; writes a benchmark run, never a promotion) |

The dashboard's **Research → V2 Rollout** page shows enforcement modes, Part 6 runs and the Phase 7
panel. `?demo=1` shows a fictional walkthrough.

### Attribution

Which layer failed: the evidence, the forecast or the option trade? `attribute_decisions` (run each collector
cycle) scores every manager decision; the report reads the stored scores. Monitoring only; it grants no authority.

```
docker compose exec worker python scripts/research.py attribution --days 0 --horizon 5
```

- `matrix`: evidence quality × direction outcome, with count and mean signed return. Each quality row also has
  its hit rate (correct ÷ correct, wrong and flat) and a bootstrap standard error.
- `by_evidence_source`: `thesis_check` vs `grounding_proxy`.
- `model_self_rating_calibration`: hit rate by the strategist's own `evidence_quality`. Flat across ratings
  means the self-rating carries no signal.
- `primary_failure_counts`, `expression` (traded decisions only) and `abstentions` (how far the stock moved
  after `neither`, against directional decisions).

`--days` keeps decisions made in the last N days (by decision time, not when they were scored); 0 means all.
The Decision Autopsy page shows the same three labels per decision.

### Pick score and confidence components

Every manager decision stores `thesis_confidence`, `timing_confidence`, `execution_confidence`,
`pick_score` and `pick_score_version` on `manager_decision_history` (confidence was backfilled from stored
output when the columns were added). The score's inputs and components are under `pick_score` in
`manager_decision_payloads.manager_output_json`; the formula is in `services/research/pick_score.py`.
It is log only: `trade_executor.py` does not order candidates by it until a ledger shows it separates
outcomes. Join it to outcomes through `manager_decision_history_id`, for example:

```
SELECT h.pick_score_version, ROUND(h.pick_score, 1) AS score, COUNT(*), AVG(o.forward_residual_return)
FROM manager_decision_history h JOIN candidate_price_observations o ON o.candidate_id = h.candidate_id
WHERE h.pick_score IS NOT NULL GROUP BY 1, 2;
```

In the first weeks, check these:

- **Thesis report:** `invalid_output_rate`. If it is high, the strategist is not producing
  checkable theses.
- **Economics report:** how often expected value is unavailable. Without price-response history,
  that is every time.
- **`forward-status`:**
  - whether mark sessions say `complete` each trading day;
  - whether cohorts enroll candidates;
  - whether paired evaluations keep up with decisions.

## From shadow to enforcement

Setting a mode to `enforce` is not enough. `guard_policy` keeps each gate in shadow until
`research_gate_promotions` holds a proof for that gate's current thresholds and source hash.

- **Gate proofs.** A proof comes from a sealed frozen-forward Part 6 run and an explicit
  `scripts/v2_part6.py promote-gate REPORT_ID STAGE`. It needs ≥100 evaluable observations, two
  regimes, complete coverage and positive gate value.
- **Pooled gate proofs.** A gate scored on the underlying move (catalyst, continuation, regime,
  thesis blocks) need not wait for one run to seal. `scripts/v2_part6.py report-gate STAGE` scores it
  on every frozen-forward row whose recorded gate-code hash and policy hash equal the current ones,
  whichever run enrolled it. It counts the first verdict per symbol, New York day and side, and only
  asks at least the 5-day horizon before the report time. `promote-gate REPORT_ID STAGE` accepts that
  report with the same thresholds. A deploy that leaves the gate's `GATE_FILES` unchanged keeps
  pooling; one that edits them starts that gate's sample again. Rows from unfinished candidates
  (rejection-only verdicts) are left out. The report records the evaluator version, and promotion refuses
  it once any evaluator file changed: re-run `report-gate`, which re-scores the same frozen rows.
- **TRADABILITY proof.** The pre-strategist check is pooled the same way but scored on precision: of its
  rejections with a known outcome, the share later hard-blocked (EXECUTION `premium_per_trade_cap` or
  CONTRACT_SELECTION `continuation_confirmed_but_no_eligible_contract`) versus sent as an order. Promotion
  needs ≥100 such rejections, passes and rejections, two regimes and precision ≥ 0.95. Once promoted,
  `TRADABILITY_MODE=enforce` skips the strategist and manager calls for a rejected pick.
- **Option-scored gates** (TRADE_ECONOMICS and the contract gates) are judged on hypothetical option P&L and
  do not pool; they need a sealed frozen-forward `--month all` report.
- **Challenger approval.** It comes from a final comparison report that met its cohort's frozen
  criteria, plus `scripts/research.py promote-challenger REPORT_ID`. This records approval only;
  you switch `STRATEGIST_MODEL`/`MANAGER_MODEL` yourself.
- **Code freeze.** Any deploy that changes the source hash invalidates proofs in progress and
  restarts cohorts. Evidence needs the code to stay unchanged for the whole window: 60 days, plus
  two days for exits.
- **Champion pin.** After `promote-champion`, any manifest change blocks all live entries until
  you re-promote.

## Cost and storage

| Item | Size or cost |
| --- | --- |
| Candidate snapshots (existing) | About 2.3 GB per 9 days on live before the snapshot packing in 88c571f; a fifth of it was the repeated manifest (see Known issues) |
| Production cohort (P7) | About 40 MB per 60-day cohort |
| Challenger on (P7) | About 670 KB per decision, about 240 MB a week; up to ~100 extra model calls a day |
| Event reviews (P6) | 3 adjudicator calls per evaluated review, at most 3 a position a day |
| Research (P3) | Up to 2 planner rounds and 6 lookups a session; reserved cost is capped by `RESEARCH_DAILY_COST_BUDGET` |

## Known issues

These come from a review of all phases and runs against a migrated copy of the live database.

1. **Every candidate snapshot embeds the full 36 KB pipeline manifest.** Partly fixed: since
   88c571f new snapshots store repeated subtrees, the manifest included, once in `json_blobs`.
   Older rows keep their full copy until `scripts/compact_db.py` repacks them. Before the fix this
   was about 0.49 GB of the 2.31 GB of snapshots in nine days.
2. **Most catalysts are never classified by the rules.** On live, 4,535 of 4,758 were
   `needs_review`. Only classified catalysts with a ticker get price responses, so economics
   scenarios, Part 4 hurdles, calibration labels and the discovery ledger's outcomes saw about 4% of
   events. Mitigated: the model classifier is on live (9,270 labels by 2026-11-04). A cheap model
   (`CATALYST_CLASSIFIER_*`) labels the rule failures. Labels live in `catalyst_model_labels`,
   survive catalyst refreshes and are read point-in-time for candidates. To turn it on elsewhere,
   run `python3 scripts/catalysts.py classify --limit N`, spot-check with `review --sample 20`, then
   set `CATALYST_CLASSIFIER_ENABLED=true` (default `false`) so each news cycle labels up to
   `CATALYST_CLASSIFIER_MAX_PER_CYCLE` (200) new ones. `status` and `pricing_report` split catalysts
   by `classification_source` (`rules` or `rules+model`). Types without a family (`supply_chain`,
   `management`, `capital_return`, `financing`) stay `needs_review` even when labelled.

Fixed since (kept here because they explain older data):

- **Research blocked the trading cycle.** Research sessions and theses ran in the worker before the
  strategist; they now run in the news collector.
- **Research paid for a planner call that could not help.** A session with no verified company
  facts now defers before calling the planner.
- **A model outage used up the day's thesis review.** A daily validation with no verdict (every
  sample failed, or an error) is now retried that day, at most 3 attempts, at least 5 minutes
  apart. `thesis_validation_runs.attempts` counts them; a retry replaces the failed run.
- **Event reviews over the daily limit were never retried.** They closed as `daily_limit_reached`;
  on the first discovery scan, 4 of 17 review items for one position did. They now wait for the
  next New York day. Older records with that status remain.
- **Dead admission path.** `_filter_company_symbols_by_high_confidence_support` accepted
  `discovery_context_by_symbol`, but no caller passed it. `enforce` routing now does.
- `.env.example` set the daily thesis validator to `live`; it now matches the live server (`shadow`).
- Part 6 forward collection never produced outcomes on live.
- Empty `""` timestamps crashed collection.
- Discovery candidates entered production runs.
- Part 6 inputs repeated the manifest and market context for every candidate.
- An invalid research setting could stop candidate creation.

See [Phase 7](RESEARCH_PHASE7.md).
