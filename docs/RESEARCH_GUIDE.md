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
| `news_collector` | Price responses and forward labels, Phase 7 challenger evaluations and reports, Phase 1 SEC collection, Phase 2 discovery scan | Every collection cycle (3 h by default) |
| `worker` | Opens production cohorts (P7). Before the strategist/manager: discovery consume (P2), research (P3), research theses (P4). During decisions: evidence thesis (P4), economics (P5), decision observations (P7). | Every trading cycle |
| `option_manager` | Event thesis reviews (P6), closing marks for research positions (P7) | Each management cycle; marks once per session ~10 min before the close |
| `api` | Read-only `/api/v2-status` and `/api/forward-evaluation` | On request |

Schema changes apply on the first start of any service after a deploy. Migrating a copy of the live
database took about 3 s.

## Defaults and dependencies

| Setting | Template default | Needs | Model cost |
| --- | --- | --- | --- |
| `COMPANY_EVIDENCE_ENABLED` | `false` | `SEC_USER_AGENT` with an `@` contact | none |
| `DISCOVERY_MODE` | `disabled` | Phase 1 for primary evidence; otherwise news only | none |
| `RESEARCH_MODE` | `disabled` | `DISCOVERY_MODE=shadow`, a planner model and, on Vertex, a cost budget and rates | planner calls |
| `EVIDENCE_THESIS_MODE` | `shadow` | Nothing on the production route; the research route needs `RESEARCH_MODE=shadow` | inside the strategist call; research theses add calls |
| `TRADE_ECONOMICS_MODE` | `shadow` | Price-response history from the collector for scenarios | none |
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
     evidence. Reviews beyond three per position per day are closed as `daily_limit_reached`,
     not retried.

3. **Research (P3/P4 research route).** Only after Phase 1 has collected facts.

   ```dotenv
   RESEARCH_MODE=shadow
   RESEARCH_PLANNER_MODEL=<model>
   # On Vertex also set RESEARCH_DAILY_COST_BUDGET and the per-million-token rates.
   ```

   It runs inside the worker's trading cycle (see Known issues).

4. **Challenger (P7), optional.**

   ```dotenv
   RESEARCH_CHALLENGER_MODE=shadow
   RESEARCH_CHALLENGER_MANAGER_MODEL=<model>   # and/or _STRATEGIST_MODEL
   ```

   Keep `RESEARCH_CHALLENGER_DAILY_CALLS` near two per production decision (about 120 today), or
   the backlog counts against coverage.

`EVIDENCE_THESIS_MODE`, `TRADE_ECONOMICS_MODE`, `EVENT_THESIS_REVIEW_MODE` and
`FORWARD_EVALUATION_MODE` are already `shadow` and need nothing.

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
| Which route admits better events? | `docker compose exec worker python scripts/research.py discovery-ledger --days 30` |
| Do research theses hold up? | `docker compose exec worker python scripts/research.py research-outcomes --days 30` |

The dashboard's **Research → V2 Rollout** page shows enforcement modes, Part 6 runs and the Phase 7
panel. `?demo=1` shows a fictional walkthrough.

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
| Candidate snapshots (existing) | About 2.3 GB per 9 days on live; a fifth of it is the repeated manifest (see Known issues) |
| Production cohort (P7) | About 40 MB per 60-day cohort |
| Challenger on (P7) | About 670 KB per decision, about 240 MB a week; up to ~100 extra model calls a day |
| Event reviews (P6) | 3 adjudicator calls per evaluated review, at most 3 a position a day |
| Research (P3) | Up to 2 planner rounds and 6 lookups a session; reserved cost is capped by `RESEARCH_DAILY_COST_BUDGET` |

## Known issues

These come from a review of all phases and runs against a migrated copy of the live database. They
are not fixed yet.

1. **Research blocks the trading cycle.**
   - `run_discovery_shadow_cycle` runs synchronously in the worker before any strategist or
     manager call. It covers discovery, then up to `DISCOVERY_BATCH_SIZE` (6) research sessions of
     up to 90 s each, then their theses.
   - Planner retries `sleep` for up to 30 s inside it.
   - With `RESEARCH_MODE=shadow`, every trading decision can start several minutes late.
2. **Research pays for a planner call that cannot help.** A session with no verified company facts
   still calls the planner before deferring. On live data, 6 of 6 sessions did.
3. **A model outage uses up the day's daily thesis review.** If every adjudicator sample fails, the
   failure is saved as that day's only run, and a second run that day is silently dropped.
4. **Every candidate snapshot embeds the full 36 KB pipeline manifest.** That is about 0.49 GB of
   the 2.31 GB of snapshots in nine days. The snapshot's own column already holds the version id.
5. **95% of catalysts are never classified.** On live, 4,535 of 4,758 are `needs_review`. Only
   classified catalysts with a ticker get price responses, so economics scenarios, Part 4 hurdles,
   calibration labels and the discovery ledger's outcomes see about 4% of events.
6. **Event reviews over the daily limit are never retried.** They close as
   `daily_limit_reached`; only the next daily review sees that evidence. On the first discovery
   scan, 4 of 17 review items for one position closed this way.
7. **Dead admission path.** `_filter_company_symbols_by_high_confidence_support` accepts
   `discovery_context_by_symbol`, but no caller passes it, so its primary-event admission branch
   never runs.

Fixed since (kept here because they explain older data):

- `.env.example` set the daily thesis validator to `live`; it now matches the live server (`shadow`).

- Part 6 forward collection never produced outcomes on live.
- Empty `""` timestamps crashed collection.
- Discovery candidates entered production runs.
- Part 6 inputs repeated the manifest and market context for every candidate.
- An invalid research setting could stop candidate creation.

See [Phase 7](RESEARCH_PHASE7.md).
