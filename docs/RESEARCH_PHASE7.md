# Phase 7: forward evidence and promotion evaluation

Phase 7 makes evidence accumulate on its own and makes promotion depend on rules written before
the evidence exists. It adds five things:

- **Rolling forward cohorts** that open automatically.
- **Decision-time entries and closing marks**, so shadow trades actually resolve.
- **An optional challenger**: an alternate strategist/manager model pair evaluated on exactly the
  inputs production saw.
- **Pre-registered acceptance criteria** frozen into every cohort, and a paired comparison report
  judged by them.
- **Route ledgers** for discovery and for the research route.

Everything is shadow research. No cohort, paired evaluation, comparison or approval can reach the
broker: paired evaluations are `paired:` candidates, which the order builder and the executor's
champion guard both refuse, like `discovery:` candidates.

## Enable and operate

```dotenv
FORWARD_EVALUATION_MODE=shadow          # off | shadow: cohorts, closing marks, outcome collection
FORWARD_MARK_LEAD_MINUTES=10            # closing marks are polled this long before the close
# Frozen into each cohort when it opens; a change applies to new cohorts only.
FORWARD_EVALUATION_WINDOW_DAYS=60
FORWARD_COHORT_CANDIDATES=10000
FORWARD_MINIMUM_PAIRED_OBSERVATIONS=100
FORWARD_MINIMUM_CLUSTERS=30
# Optional challenger: off | shadow
RESEARCH_CHALLENGER_MODE=off
RESEARCH_CHALLENGER_STRATEGIST_MODEL=   # empty inherits production's model
RESEARCH_CHALLENGER_MANAGER_MODEL=
RESEARCH_CHALLENGER_PER_CYCLE=12
RESEARCH_CHALLENGER_DAILY_CALLS=120
```

Cohorts and marks need no other settings. The challenger needs at least one model. It uses
`LLM_PROVIDER` and the production prompts, and costs up to two model calls per production decision
(about 50 decisions a day today).

Inspect without network or model access:

```bash
python3 scripts/research.py --db /path/to/market.db forward-status
python3 scripts/research.py --db /path/to/market.db compare COHORT_ID      # writes a report
python3 scripts/research.py --db /path/to/market.db comparison REPORT_ID
python3 scripts/research.py --db /path/to/market.db discovery-ledger --days 30
python3 scripts/research.py --db /path/to/market.db research-outcomes --days 30
python3 scripts/research.py --db /path/to/market.db promote-challenger REPORT_ID
```

`discovery-ledger` ends with `promotion_check`, the bar for setting `DISCOVERY_MODE=enforce`. It was
fixed on 2026-11-04 before any results were read, and is evaluated on 5-day outcomes:

- at least 14 days since the first discovery admission;
- at least 40 resolved discovery-only events (events the ranking never sent to the strategist);
- their mean absolute residual move and direction hit rate both at least the ranking-sourced events'.

`failures` names each unmet criterion. Companies that enforce later routes into production still count as
discovery-only, so the comparison stays readable after the switch.

`forward-cycle` runs the collector's forward step once and, with a challenger on, calls models.
The dashboard's **Research → V2 Rollout** page shows the same reading (`/api/forward-evaluation`)
and has a fictional demo (`?demo=1`).

## How it works

```text
worker:      begin_candidate -> production cohort opened if none is open for this manifest
             manager decides -> (challenger on) freeze decision observation:
                strategist + manager inputs, every DTE bucket's chain, catalysts
             unchanged production selection, gates and orders
collector:   challenger runner -> incumbent's recorded outputs and the challenger's fresh ones
                finalized on the same frozen inputs, as of the observation cutoff
             Part 6 collection (existing) -> research thesis outcomes -> one interim report a day
option mgr:  ~10 min before the close -> collect cohorts, poll Alpaca quotes for open positions
```

### Cohorts

A cohort is one frozen-forward Part 6 run per strategy, registered with its acceptance criteria
and evaluation window in `research_run_acceptance`.

- **Production cohort.** Opened by `begin_candidate` just before a production candidate is
  recorded, for that candidate's own manifest. A cohort therefore always starts before any decision
  it enrolls. A deploy that changes the manifest opens a new cohort; the old one keeps maturing.
- **Comparison cohort.** An incumbent run and a challenger run, opened together when the first
  decision observation for a manifest and challenger is frozen. They share a window and criteria.
- **Window.** Enrollment covers decisions made before the window ends. A run seals two days later,
  so in-flight decisions and the challenger backlog can still enroll, or when its candidate target
  is reached. A sealed or expired cohort is replaced by a new one (rolling).
- **Routes.** Production cohorts no longer enroll `discovery:` or `paired:` candidates. Paired
  cohorts enroll only their own role's paired candidates.

### Decision observations

While a challenger is on, after the production manager returns, the live route freezes one
`research_observation` (protocol `decision-observation-v1`). It holds:

- **Strategist input.** The exact `pre_strategist` payload.
- **Manager input.** The `pre_manager` payload without the strategist's output. Evidence identical
  to the strategist's is stored once.
- **Option chains.** Every DTE bucket's chain from one 12–60 DTE fetch, split locally with the same
  nearest-strike rule a bucket refresh uses.
- **Catalysts.** The candidate's catalysts at creation.

The cutoff is when the chain fetch returned. Nothing a strategy produced is in the observation.
Production's candidate is only linked to it (`decision_observations`), never bound, because its
own snapshots predate it. A failure (missing checkpoint, evidence after the cutoff) records the
observation as `unavailable` with the reason and never raises into the cycle.

### Paired evaluations

For each observation the collector produces two research candidates bound to it:

| Role | Model outputs | Model calls |
| --- | --- | --- |
| Incumbent | Production's recorded strategist and manager outputs | none |
| Challenger | Fresh outputs from the challenger models on the identical frozen payloads | 0–2 |

A stage whose model is unchanged reuses the incumbent's output. The manager is called again
whenever the strategist changed, since its input changed. Both roles then pass through the same
deterministic finalization, with the clock pinned to the observation cutoff (`evaluated_at`):

- the evidence-continuation gate;
- contract selection from the frozen chain of the bucket that role chose;
- the regime gate and the evidence-thesis gate;
- trade economics, only once it is enforced (shadow economics cannot change a decision).

Selecting both roles from the same frozen chain keeps the comparison on identical information.
Production's actual selection, made from a chain fetched after its decision, still feeds the
Part 6 comparison against baselines.

| Status | Meaning |
| --- | --- |
| `evaluated` / `abstained` | Finalized; directional, or neither |
| `pending` | Retrying after an error (two attempts), or waiting for the daily call allowance |
| `unavailable` | See the reason below; the pair can never resolve and counts against coverage |

Unavailable reasons:

- `implementation_changed`, `configuration_changed`, `prompt_changed`: the current code, gate
  configuration, selector settings or prompts differ from the decision's manifest.
- `incumbent_unavailable`: no challenger calls are spent on a pair that cannot resolve.
- `cohort_sealed`.
- `interrupted_during_evaluation`: a model asked again could answer differently, so it is not.
- `evaluation_error:*` or `finalization_error:*`.

Challenger thesis checks are stored with the paired candidate and kept out of the Phase 4 thesis
report. Its strategist run never writes a strategist summary production reads.

### Entries and closing marks

- **Entry.** The ask from the quote frozen with the decision, when it is at most 900 s older than
  the cutoff. These quotes are Alpaca's.
- **Marks.** The option manager polls Alpaca's latest quote for every still-open shadow contract,
  once per New York session, `FORWARD_MARK_LEAD_MINUTES` before the close. The poll first runs
  collection, so today's decisions are covered. Marks are stored in `historical_option_quotes` with
  provider `alpaca`, so entry and exit always come from one vendor.
- **Missed sessions.** A missed session is a permanent gap; the simulation reports
  `missing_daily_marks` rather than inventing a price.
- **Exits.** Exits use Part 6's shared research policy at the closing bid. Daily marks cannot
  evaluate intraday exits or limit-order repricing, so those claims are not evaluated.

### The comparison report

`comparison_report(cohort_id)` pairs the incumbent and challenger outcomes for every recorded
observation. Each persisted report is immutable, and the collector writes one interim report per
cohort per day.

- **Pairs.** A pair counts only if both outcomes are resolved (`complete` or `cash`). A missing
  quote, an unevaluated observation or a pending exit removes the pair and lowers coverage. It is
  never a flat trade, so missing data cannot appear as an avoided loss.
- **Clusters.** Decisions on the same announcement (primary catalyst), or on one company on the
  same day, form one cluster. The live pipeline re-evaluates an announcement every cycle: in the
  live snapshot, 305 manager decisions came from 24 announcements.
- **Uncertainty.** A one-sided cluster bootstrap of the mean paired difference resamples whole
  clusters. It is seeded from the observation set, so a report can be reproduced.
- **Also reported:** net P&L and return on capital at risk, win rate, worst trade, maximum
  drawdown (by exit time), the challenger against each baseline and benchmark on the same pairs,
  confidence calibration, challenger model calls and latency, and event-to-decision time.
- **Final.** A report is final only after the window plus two days, with both runs sealed and no
  exit or evaluation pending. Until then its verdict is `interim`.

### Pre-registered acceptance (`forward-acceptance-v1`)

| Criterion | Default | Notes |
| --- | --- | --- |
| Paired resolved observations | ≥ 100 | Part 6 floor; cannot be lowered |
| Independent clusters | ≥ 30 | |
| Regimes among pairs | ≥ 2 | Part 6 floor; cannot be lowered |
| Coverage (resolved pairs / all observations) | ≥ 90% | Unavailable observations count against it |
| Paired net P&L, challenger minus incumbent | > 0 | |
| Cluster-bootstrap lower bound (90%) | > 0 | |
| Beats every baseline and benchmark | each > 0 | On the same pairs, with ≥ 1 resolved |
| Challenger max drawdown | ≤ 1.25 × incumbent's | |
| Daily marks verified | all trades | |

The criteria are hashed into each run's registration and the table is immutable. A report always
uses its cohort's frozen criteria, never the current environment.

### Approval

`promote-challenger REPORT_ID` requires all of the following:

- a final report whose criteria all passed;
- registrations matching the report;
- both runs' evaluator identity current;
- the current source hash equal to both runs' frozen one.

It then records an immutable `challenger_approvals` row. It does not switch production's models
and does not touch the champion pin. The operator sets `STRATEGIST_MODEL`/`MANAGER_MODEL`, and
the switched configuration starts its own production cohort. A pinned production manifest (from
Part 6 `promote-champion`) blocks the switched configuration until it is unpinned or re-promoted.

### Discovery and research routes

- **Discovery ledger.** Every event (catalyst) seen in the window gets a status for each route:
  admitted, deliberately not admitted (with its reason), or never evaluated. Here, "production"
  is the sector/opportunist admission filter and "discovery" is Phase 2 intake.
  - Admitted events are grouped as both, production-only or discovery-only, and scored with the
    catalyst's stored residual response at 1–10 days, signed by its direction.
  - Events never registered for response collection stay unresolved, never zero.
- **Research route.** Each directional Phase 4 research thesis is scored on the stock's residual
  move in its direction, from the thesis time to its maximum horizon (snapped down to a stored
  horizon). Moves before the thesis existed are not credited.
  - It is reported by check status, beside the sessions' reserved cost, call counts, latency and
    model/prompt versions.
  - Its tickers join the collector's history refresh.

## Screening before a cohort

A frozen-forward cohort is slow (about 60 days), so candidate challengers are first screened on a
fixed benchmark of past decisions. The benchmark only ranks challengers. Only a frozen-forward
cohort can promote one: nothing here writes `research_gate_promotions`, and `promote-challenger`
never reads a benchmark.

```
python scripts/research.py benchmark create --set-id bench-2026q3 --size 300 --holdout 0.3
python scripts/research.py benchmark show bench-2026q3
python scripts/research.py benchmark run bench-2026q3 --model stored --split dev
python scripts/research.py benchmark run bench-2026q3 --model MODEL --split dev [--replay-strategist]
python scripts/research.py benchmark compare bench-2026q3 --a stored --b MODEL --split dev
python scripts/research.py benchmark run bench-2026q3 --model MODEL --split holdout --confirm-holdout
```

- **The set.** `create` picks replayable decisions (frozen input present, snapshot hashes valid), one per
  symbol per 24 hours, balanced over sector, direction, regime, month and (once labelled) catalyst
  family. Whole symbols go to holdout, so no symbol is in both splits. The selection is stored with
  every parameter, and the same set id always selects the same items. Sets, items and runs are
  insert-only. Abstentions (`neither`) never exceed 40% of the items actually selected, so a thin pool
  gives a smaller set, not a skewed one.
- **A run** replays the split's decisions with the model, then scores each in three layers: evidence
  (the replayed strategist's thesis check with `--replay-strategist`, otherwise production's, reported
  as fixed), direction (hit rate and signed return at 5 and 10 trading days, with bootstrap SEs, beside
  the unconditional stock-move baseline; `hit_rate` treats a move within 0.5% as a miss, so compare
  `sign_hit_rate`, the share of graded calls whose signed return was positive, with the baseline) and expression (option P&L where a trade was simulated).
  `--model stored` is the sanity baseline: its direction hit rate equals a direct `--score-direction`
  over the same ids.
- **Compare** pairs two runs item by item and reports, per layer, the mean difference (b minus a) with a
  bootstrap SE, plus direction wins, losses and ties. An item counts only if both runs scored it; the
  rest are listed with each run's reason (`excluded_pre_knowledge_cutoff`, `model_error`, ...).
  `--a MODEL:PROMPTS` picks the run with that manager prompt version.
- **Holdout is single-use.** Score it once per set and model, after choosing on dev. A second holdout
  run of that model (new prompts or strategist flag included) is refused before any model call, and the
  CLI needs `--confirm-holdout`. A fresh holdout score for a revised prompt needs a new set.
  Dev runs can repeat.
- **Contamination.** A model may remember decisions near its training data. Unless the model is
  `stored`, decisions up to 30 days after the model's knowledge cutoff are dropped and counted
  (`excluded_pre_knowledge_cutoff`, with the cutoff used in `scores.replay.contamination`), and a
  model with no known cutoff is refused unless `--allow-unknown-cutoff`. To add a model, append a row
  `model,knowledge_cutoff,source` to `config/model_knowledge_cutoffs.csv`: `source` is the provider
  documentation URL, or `release-date-proxy`. Ids match exactly (ignoring case), so list each dated
  variant on its own row.

## Safety boundaries

- **No broker path.** Paired and discovery candidates are refused by the order builder and the
  champion guard. The runner has no broker access. Marks only read quotes.
- **Production unchanged.** Observation freezing and cohort opening run under `observe` and never
  alter a production decision, gate or order. With the challenger off, the live route only opens
  production cohorts.
- **Misconfiguration is recorded, not raised.** An invalid challenger, acceptance or event-review
  setting is written into the manifest as a `configuration_error`. It never stops production
  candidates from being created.
- **Separate from production views.** V2 status activity, the candidate funnel and Part 5 contract
  observations exclude research candidates, so paired evaluations never look like production
  verdicts or blocks.

## Storage and cost

Measured on a copy of the live snapshot:

- **Decision observations:** about 390 KB each, of which about 330 KB is the strategist input.
- **Paired candidates:** about 140 KB each.
- **Per decision:** about 670 KB in all, or roughly 240 MB a week at about 50 decisions a day,
  while a challenger is on.

Two choices keep this down:

- Bound candidates store a digest of the observation as their witness, not a full copy.
- Paired snapshots carry a slim manifest.

Part 6 inputs no longer repeat the 37 KB manifest or the full market context per candidate, which
brings a 60-day production cohort to about 40 MB. Freezing chains adds roughly three Alpaca calls,
and a few seconds, to each manager decision while a challenger is on.

## Design decisions

- **The worker opens cohorts.** A cohort must start before its decisions, and only the worker can
  name its own manifest: other containers' environments differ, so they build different manifests.
- **Freeze after the incumbent decides.** The models see exactly what production's models saw. The
  frozen chain is used only by deterministic selection, never shown to a model.
- **Re-select both roles from the frozen chain.** Production's own bucket chain is fetched after its
  decision, so no challenger choosing another bucket could otherwise be finalized.
- **Reuse unchanged stages.** Re-calling an unchanged model would add noise and cost without
  testing the challenger.
- **The challenger runs in the collector.** It adds no latency to trading; the frozen inputs make
  the later call equivalent.
- **Cluster by announcement and company-day, not by day.** Linking whole days collapses every live
  cohort into one cluster, because announcements recur for days.
- **One vendor for entry and marks.** The evaluator never splices providers; MarketData.app EOD
  quotes cover production selections only, and only at end of day.
- **Approval records evidence only.** No other process can predict the manifest the worker builds
  after a model switch, so approval cannot pin it.

## Limitations

- **Market-wide clustering.** Correlation between unrelated decisions on one day (market-wide
  moves) is not clustered; the two-regime floor is the only time-diversity check.
- **Daily marks only.** Intraday exits, stops and limit repricing are not evaluated. Alpaca quotes
  are whatever feed the account has (indicative without an OPRA subscription).
- **Chain approximation.** Bucket chains use the manager input's reference price, minutes old at
  most; contract numbering can differ from a live refresh, which matters only for exact ties.
- **Model challengers only.** A prompt challenger needs code and a new version.
- **Deploys restart the clock.** Any deploy that changes the source hash opens new cohorts, and a
  cohort's approval needs the current code to equal its frozen code. In practice the code must stay
  unchanged for the whole window plus two days.
- **Coverage depends on throughput.** The challenger runs on the collector's three-hour cycle; an
  allowance below two calls per decision builds a backlog that counts against coverage.
- **Shadow gates.** Shadow economics and option hurdles are not replayed for paired evaluations.

## Existing behaviour found during implementation

1. **Part 6 forward collection could not produce evidence on the live system.**
   - No run had ever been started; starting one was manual.
   - All 2,145 cached option quotes were MarketData.app end-of-day quotes, fetched only for
     production-selected contracts.
   - Entries need a quote within 15 minutes before the decision, so every counterfactual would have
     been `missing_entry_quote`.
   - Fixed by decision-time entries, closing marks and automatic cohorts.
2. **Empty timestamps crashed collection.**
   - Index, sector-ETF and unquoted contract quotes are serialized with `timestamp: ""`. The
     cutoff check raised on them instead of skipping them, and one such candidate paused its whole
     run.
   - The same check would have rejected every decision observation.
   - Fixed: an empty time is treated as absent, like `None`.
3. **Discovery candidates entered production runs.** They carry the production manifest, so Part 6
   enrolled them as unavailable decisions. Once discovery was on, `promote-champion`'s
   no-missing-outcome rule would have refused every production run. Fixed by the route filter.
4. **Every candidate snapshot embeds the full 36 KB pipeline manifest.** In the live snapshot that
   is about 0.49 GB of the 2.31 GB of snapshots, written over nine days. It is not changed here
   (the envelope has other readers); paired evaluations write a slim manifest instead.
5. **Part 6 inputs repeated the manifest and full market context per candidate.** That is about
   40 KB per candidate even for admission rejections, and hundreds of KB for decisions. Fixed:
   only a replayable missing selection keeps its market context.
6. **One announcement, many decisions.** 305 manager decisions over six days came from 24
   announcements, 18 of them re-evaluated on several days. Part 6's 100-sample promotion floor
   counts decisions, not independent events.
7. **The chosen bucket's chain is fetched after the manager decides.** In the live snapshot, none
   of the 22 selected contracts were in the chain the manager saw.
8. **Price-response collection had produced nothing in the live snapshot.**
   - `catalyst_response_events` was empty although 188 classified catalysts with tickers existed.
   - Price history covered only SPY and VIX, and no candidate had a forward label.
   - Trade-economics scenarios, Part 4 hurdles, calibration labels and this phase's discovery
     outcomes all depend on that collection.
9. **The champion pin blocks every later deploy.** After `promote-champion`, any manifest change
   stops all live entries until re-promotion, including a switch to an approved challenger's
   models.
10. **Part 6's default target counts every candidate.** That includes the roughly 90% rejected at
    admission, so a 200-candidate run filled in about a day and covered one regime. Automatic
    cohorts use a 60-day window with a 10,000-candidate cap instead.

## Code and validation

| Concern | Location |
| --- | --- |
| Policies | `Python Scripts/services/evaluation/forward_evaluation_policy.py` |
| Cohorts and paired manifests | `Python Scripts/services/evaluation/forward_cohorts.py`, `Data/market_db_modules/forward_evaluation.py` |
| Decision observations | `Python Scripts/services/evaluation/decision_observations.py`, `option_chain.build_bucket_chains` |
| Challenger runner, finalization | `Python Scripts/services/evaluation/challenger_evaluation.py`, `paired_decisions.py`, `evaluation_clock.py`, `strategist_runner.replay_strategist_decision_from_payload` |
| Closing marks | `Python Scripts/services/research/research_marks.py`, option manager loop in `worker_application.py` |
| Comparison, acceptance, approval | `Data/market_db_modules/forward_comparison.py` |
| Discovery and research routes | `Data/market_db_modules/discovery_evaluation.py`, `research_outcomes.py` |
| Collector step | `Python Scripts/services/evaluation/forward_evaluation.py`, `news_collector_main.py` |
| Part 6 changes | `research_runs.py` (registration, window, routes, decision-quote entry), `research_inputs.py`, `research_observations.py` (digest witness) |
| Schema | `Data/market_db_modules/forward_evaluation_schema.sql` |
| API and dashboard | `api_support/forward_evaluation.py`, `api_routes/forward_evaluation.py`, `web_dashboard/src/forwardEvaluation*.{ts,tsx,css}` |
| Tests | `tests/test_research_forward_collection.py` |

```bash
DB_PATH=/tmp/phase7.db TELEMETRY_ENABLED=false PYTHONPATH=tests:. \
  python3 -m unittest tests.test_research_forward_collection
```

The 71 Phase 7 tests use isolated databases and fixed clocks, with no model, provider or broker
calls. They cover the plan's acceptance cases:

- A genuinely different strategy is paired with the incumbent and evaluated end to end, through
  the real selector and gates.
- A 105-observation cohort meets its criteria and is approved, and a worse one is not.
- Missing quotes cannot count as avoided losses.
- Future evidence is rejected from an observation and from a paired decision.
- Paired candidates are refused by the order builder and the champion guard.

They also cover a misconfigured challenger or acceptance setting, which is recorded in the manifest and
never stops the trading cycle, plus cohort opening, rolling and immutability, the challenger call cap and backlog,
retries, implementation drift, stage reuse, closing-mark sessions, clustering, bootstrap
reproducibility, both route ledgers and the read-only API.

On a copy of the live snapshot:

- Migration took 2.7 s, with integrity `ok` and no foreign-key violations.
- A leakage-checked run collected all 1,656 candidates in 23 s; before the empty-timestamp fix it
  failed on the first decision.
- Twelve real decisions froze into observations and were finalized as incumbent and challenger
  (stub models) through the real selector and gates.
