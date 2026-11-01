# Plan B: point-in-time replay benchmark (FinanceGym-style screening)

Status: proposed (2026-10-27). See [README.md](README.md) for the index.

## Context
FinanceGym evaluates agents on a fixed, balanced question set with per-question information
cutoffs, so systems can be compared quickly and repeatably.

Here, the only evidence that counts toward promotion is a Phase 7 frozen-forward cohort: about
60 days of unchanged code, plus 2 for exits. That is slow for iterating on prompts and models.

[`services/evaluation/llm_backtest.py`](../../Python%20Scripts/services/evaluation/llm_backtest.py) can already replay a model over frozen decision inputs. Three things
are missing before it can work as a benchmark:

1. **A fixed item set.** Runs today are ad hoc windows (`--start-at/--end-at/--limit`), so two
   runs rarely cover the same decisions, and nothing stops a prompt from being tuned against the
   decisions it is scored on.
2. **A model-memory (contamination) guard.** The frozen inputs are PIT-correct:
   `decision_evidence_snapshots` keeps only articles published and ingested by `as_of`, and
   `FrozenDecision.load` verifies snapshot hashes. The model itself is not: a model trained past
   a decision's date may simply know what happened. Nothing records model knowledge cutoffs.
3. **Scoring that separates evidence from forecast.** Today only `llm_direction_score`
   (direction) and the option backtest (P&L) exist.

**Data reality on the server backup:**

| Item | Value |
|---|---|
| Decisions with frozen `replay_input` | 2,140, from 2026-07-21 to 2026-10-08 |
| Symbols | 64 |
| Option chains for P&L replay | saved only from late September 2026 |
| Decisions with a primary `candidate_catalysts` row | 306, all family `other` |

Because of the last two rows, direction (from daily closes) is the main metric. Strata use
sector, direction, regime and month until plan A populates catalyst families.

**Outcome.** You can build a frozen, versioned benchmark set once. Each candidate model or prompt
runs on its dev split; the holdout split is scored once at the end; runs are compared paired,
item by item, with bootstrap SEs. It is a **screen** for choosing which challenger enters a
Phase 7 cohort. It never satisfies `research_gate_promotions` or `promote-challenger`.

**Depends on plan C** for the evidence and direction scoring functions and the bootstrap helper.
Build C first.

## Changes

### 1. Model knowledge cutoffs
New `config/model_knowledge_cutoffs.csv`, with columns `model,knowledge_cutoff,source`. `source`
is the provider documentation URL or "release-date-proxy".

New `services/evaluation/model_cutoffs.py`:
- `knowledge_cutoff(model) -> date | None` matches the exact model id, ignoring case. There is no
  prefix matching: a family prefix would give a newer model an older cutoff, so every id (dated
  variants included) is listed on its own row.
- `CONTAMINATION_MARGIN_DAYS = 30`.
- `eligible(decision_run_at, model) -> 'eligible' | 'pre_knowledge_cutoff' | 'cutoff_unknown'`.

The rule: a decision is eligible only if
`decision_run_at > knowledge_cutoff + margin`.

### 2. Item filter in the replay path
- **Store:** add `ids: Sequence[int] | None = None` to `list_frozen_manager_decisions`
  ([`Data/market_db_modules/llm_replay_store.py:16`](../../Data/market_db_modules/llm_replay_store.py#L16)). It adds
  `AND history.id IN (...)` (chunked if the list is long), and the order stays chronological.
- **Runner:** in [`services/evaluation/llm_backtest.py`](../../Python%20Scripts/services/evaluation/llm_backtest.py), thread `decision_ids` through `ReplayRun.build`
  (:451) and `run_llm_backtest` (:565).
- **Contamination guard inside `ReplayRun.build`:**
  - Unless `model == STORED_MODEL`, drop `pre_knowledge_cutoff` decisions and count them in
    `summary()` as `excluded_pre_knowledge_cutoff`.
  - If any decision is `cutoff_unknown`, raise `ValueError`, unless the new parameter
    `allow_unknown_cutoff=True` is passed.
  - Record `{knowledge_cutoff, margin_days, source}` in `metrics.replay.contamination`.
- **Direction scorer:** `services/evaluation/llm_direction_score.run_direction_score` shares
  `ReplayRun.build`, so it inherits both the item filter and the guard. Add the same parameters
  to its signature.
- **CLI:** [`agentCallers/llm_backtest.py`](../../Python%20Scripts/agentCallers/llm_backtest.py) gets `--decision-ids-file` and
  `--allow-unknown-cutoff`, so ad hoc runs are guarded too.

### 3. Benchmark sets
**Schema** ([`Data/market_schema.sql`](../../Data/market_schema.sql), insert-only with immutability triggers, like
`research_foundation.py:14-20`):
```sql
CREATE TABLE IF NOT EXISTS benchmark_sets (
  set_id TEXT PRIMARY KEY, selection_version TEXT NOT NULL, selection_json TEXT NOT NULL,
  item_count INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS benchmark_items (
  set_id TEXT NOT NULL REFERENCES benchmark_sets(set_id), manager_decision_history_id INTEGER NOT NULL,
  split TEXT NOT NULL CHECK (split IN ('dev','holdout')), stratum TEXT NOT NULL,
  PRIMARY KEY (set_id, manager_decision_history_id));
CREATE TABLE IF NOT EXISTS benchmark_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, set_id TEXT NOT NULL, split TEXT NOT NULL CHECK (split IN ('dev','holdout')),
  backtest_run_id INTEGER, model TEXT NOT NULL, prompt_versions_json TEXT NOT NULL, replay_strategist INTEGER NOT NULL,
  scores_json TEXT NOT NULL, item_scores_json TEXT NOT NULL, created_at TEXT NOT NULL);
-- Dev runs may repeat; the holdout is scored once per set and model, so prompt tuning cannot peek at it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmark_runs_holdout_once
  ON benchmark_runs(set_id, model) WHERE split = 'holdout';
-- BEFORE UPDATE and BEFORE DELETE triggers on all three tables RAISE(ABORT, 'Immutable benchmark record').
```
- The store module is `Data/market_db_modules/benchmarks.py`.
- **Holdout runs are once only.** The partial unique index refuses a second `holdout` run for the same
  set and model, whatever its prompts or strategist flag; `run_benchmark` checks first and raises before any
  model call. A new prompt version is judged on dev; a fresh holdout score needs a new set.
  Dev runs may repeat, which is why the key is an autoincrement `run_id`.

**Selection** (`services/evaluation/benchmark_selection.py: select_items(decisions, *, set_id, size,
holdout_share, seed=None) -> list[item]`):
- **Pool.** Replayable decisions only (frozen input present, snapshot hash valid). Optionally
  restrict to a date window.
- **Stratum key.** `sector_key | production_direction(call/put/neither) | regime_label | month`.
  - `regime_label` comes from `market_context.market_regime` in the frozen input.
  - Once plan A has run, `catalyst_family` is appended from the primary `candidate_catalysts`
    row when it is not `other`.
- **Per-symbol cap (entity budget).** Apply `space_decisions_per_symbol(min_hours=24)` first, so
  neighbouring re-decisions do not repeat each other. Then cap each symbol at
  `ceil(size / symbols × 2)`.
- **Day weighting.** Following FinanceGym, sample days with weight
  `(day volume / mean daily volume) × (1 + diversity)`, a relative volume because a z-score can be
  negative. Volume is that day's decision count, and diversity is that day's distinct-sector entropy. Within a stratum, sample proportionally with the seeded
  RNG.
- **Balance.** Proportional allocation across strata with a floor of 2 per non-empty stratum.
  `neither` is capped at 40% of the set, so abstentions do not dominate.
- **Split.** Assign holdout per symbol, not per item, so a symbol's decisions never straddle
  dev and holdout. This avoids leakage through repeated situations.
- **Deterministic.** The seed defaults to `int(sha256(set_id))` (an explicit `seed` overrides it), and `selection_json` records every
  parameter.

### 4. Run and score (`services/evaluation/benchmark_runner.py`)
`run_benchmark(set_id, *, split, model, replay_strategist, allow_unknown_cutoff=False, db_path=None)`:
1. Load the split's item IDs, then call `run_llm_backtest(model=..., decision_ids=ids,
   replay_strategist=...)`. It returns the backtest run (persisted to `backtest_runs` and
   `backtest_events`) and its `metrics.replay` summary, including contamination exclusions.
2. Score each item, reusing plan C:
   - **Evidence:** with `replay_strategist`, use the replayed strategist answer's attached thesis
     check (`StrategistReplay.checked_recommendation` already attaches it; [`llm_backtest.py:168`](../../Python%20Scripts/agentCallers/llm_backtest.py#L168)).
     Run it through `decision_attribution.evidence_layer`. Without strategist replay, evidence is
     production's and reported as `fixed`.
   - **Direction:** `decision_attribution.direction_layer` on the replayed manager decision.
   - **Expression:** from `backtest_events.realized_pnl_pct`, where the option replay produced a
     trade.

   To collect per-item answers, expose an `on_answer` callback, or return the answers from
   `run_llm_backtest`. The minimal change is to add `answers` to the returned dict.
3. Aggregate:
   - per-layer rates (evidence quality mix, direction hit rate at 5 and 10 days, mean signed
     return, abstention rate, expression capture rate);
   - by stratum;
   - against the unconditional stock-move baseline (`llm_direction_score.summarize`'s
     `unconditional_stock_moves`).

   Bootstrap SEs (n=1000, seeded) come from plan C's helper.
4. Persist to `benchmark_runs`.

`compare_runs(run_a, run_b)` pairs items by `manager_decision_history_id` and reports, for each
layer:
- the paired delta and its bootstrap SE;
- the win/loss/tie counts on direction;
- the items excluded from either run, for contamination or unavailability.

Only items scored in both runs count.

### 5. CLI ([`scripts/research.py`](../../scripts/research.py))
New subcommand group `benchmark`:
- `benchmark create --set-id NAME --size 300 --holdout 0.3 [--start-at --end-at]`
- `benchmark show SET_ID`: strata counts and the split summary.
- `benchmark run SET_ID --model M [--split dev|holdout] [--replay-strategist] [--allow-unknown-cutoff]`
  - `holdout` asks for `--confirm-holdout`.
- `benchmark compare SET_ID --a MODEL[:PROMPTS] --b MODEL[:PROMPTS] [--split dev]`

All of these print JSON, following the existing pattern ([`scripts/research.py:9-124`](../../scripts/research.py#L9-L124)). Real
model runs require the usual provider env. `--model stored` is the sanity baseline.

### 6. Docs
- [`docs/RESEARCH_PHASE7.md`](../../docs/RESEARCH_PHASE7.md), new section "Screening before a cohort":
  - the benchmark ranks candidate challengers;
  - only a frozen-forward cohort can promote;
  - holdout is single-use;
  - the contamination rule and how to add a model's cutoff.
- [`docs/RESEARCH_GUIDE.md`](../../docs/RESEARCH_GUIDE.md): add a row to the "Watching it" table.
- [`docs/CODE_MAP.md`](../../docs/CODE_MAP.md): add the new modules.

## Tests
**`tests/test_research_benchmark.py`**, using `DatabaseFixture` from
[`tests/test_llm_backtest.py:39`](../../tests/test_llm_backtest.py#L39) with synthetic decisions across symbols, sectors and days.
- **Selection:**
  - the same `set_id` gives identical items;
  - a different seed gives different items;
  - the stratum floor and the `neither` cap hold;
  - the per-symbol cap holds;
  - holdout and dev share no symbol.
- **Immutability:** items and sets reject UPDATE and DELETE; a second holdout run is refused.
- **Cutoffs:**
  - exact, case-insensitive matches, and an unlisted variant of a listed id has no cutoff;
  - the margin boundary (on the cutoff day + 30, excluded; the day after, included);
  - `cutoff_unknown` raises without the override;
  - `stored` bypasses the guard.
- **Runner:** with `decide=Mock` (pattern at [`tests/test_llm_backtest.py:136`](../../tests/test_llm_backtest.py#L136)), only the set's
  IDs are replayed; the summary has the exclusion counts; item scores use plan C's layers.
- **Comparison:** paired deltas on a hand-built pair of runs, and the exclusion bookkeeping.

**[`tests/test_llm_backtest.py`](../../tests/test_llm_backtest.py) additions:**
- `decision_ids` filters while keeping chronological order;
- `limit` still behaves when combined with `decision_ids`;
- the contamination exclusion appears in `metrics.replay`.

## Verification
1. Run `python3 scripts/project.py test`.
2. On a throwaway copy of the live database backup:
   - [`scripts/research.py`](../../scripts/research.py)` --db <copy> benchmark create --set-id bench-2026q3 --size 300 --holdout 0.3`
   - then `benchmark show`, and check the strata look sensible.
3. Run `benchmark run bench-2026q3 --model stored --split dev`. Production's own answers must
   reproduce the production decisions, so the direction hit rate equals a direct
   `llm_direction_score` over the same IDs.
4. Run one real model with a known cutoff, then `benchmark compare` against `stored`. Confirm the
   paired output and that any pre-cutoff exclusions are reported.
