# Plan: closing the seven gaps that stop an edge from forming

Project: `~/code/Stock-trading-experiment` (WSL, Ubuntu). All paths below are relative to it.

## Context

The database review found seven things missing in the DeepSeek era: a market view and short side, reliable
news timing, an expected-move-versus-cost check, a way to rank picks, independent evidence, a tradable list,
and scoring of every pick.

Reading the code changed the picture: **most of these already exist as log-only ("shadow") checks.** They never
block a trade because enforcement needs a promotion proof (`services/research/research_promotion.py`
`guard_policy` → `approved`: 100+ evaluable samples, positive gate value, two regimes), and no proof has ever
been produced:

- `research_gate_promotions` and `research_reports` are empty.
- All 15 `frozen_forward` runs are still `open` with a 10,000-candidate target. A new run starts on almost
  every deploy because a run only enrols candidates whose whole-pipeline source hash matches
  (`Data/market_db_modules/research_runs.py` `collect_run` / `same_strategy`), and the docs require a 60-day
  code freeze. With near-daily commits, no run can ever seal.

So the general implementation is mostly: make evidence accumulate despite deploys, add the two entry rules the
data supports, then promote checks one at a time. Decisions taken with the user: keep the proof rule (no
override), and plan all seven gaps as ordered phases that can each ship alone.

Constraints that apply to every phase:

- New checks follow `docs/FEATURE_PLAYBOOK.md` "Entry Gate": verdict dict in a `services/gates/` module, one
  `EntryGate` in `services/gates/entry_gates.py`, `record_gate`, a policy block in
  `services/core/pipeline_version.py` and `GATE_BLOCKS`/`GATE_FILES`, default shadow.
- Schema changes are idempotent steps in `Data/market_db_modules/bootstrap.py` (`_setup_steps`).
- No model replays (Gemini 2.5 or DeepSeek) are run as part of this work. Verification uses stored data and
  forward shadow logging only.
- Never test against the live database; use a copy of the backup `E:\StockBackups\stock_experiment.latest.db` or verify snapshot via `DB_PATH`.

## Phase 0 — Measurement and proof throughput (gap 7; unblocks everything else)

1. **Pooled gate proofs across deploys.** Each enrolled candidate already stores per-gate code hashes
   (`inputs['gate_code'] = gate_code_hashes(...)` in `collect_run`), and `policy_hash` already excludes modes.
   Add a pooled report in `Data/market_db_modules/research_reports.py` that selects candidates from every run
   whose `gate_code[stage]` and policy hash equal the current ones, and scores them with the existing
   `evaluate_gate_on_moves` (`services/research/shadow_portfolio.py`, 5-day residual move, counts candidates
   the manager passed on). Let `promote_gate` accept that pooled report for move-basis gates, keeping the same
   thresholds (≥100 evaluable, passed and rejected both non-empty, two regimes, positive value, ≥90% coverage)
   and a closed window (cutoff at least the horizon before the report time). Expose it as
   `scripts/v2_part6.py report-gate STAGE` / `promote-gate`.
   Status: done — `pooled_gate_report` and pooled branch of `promote_gate` in `research_reports.py`; CLI
   `report-gate`; tests in `tests/test_v2_part6.py` `PooledGateProofTests`. No schema change.
2. **One sample per company per day.** In the pooled report, keep the first verdict per symbol, day and side so
   repeated asks do not inflate the sample.
   Status: done — first verdict per symbol, New York date and proposed side, inside `pooled_gate_report`.
3. **Gate ledger command.** `scripts/research.py gate-ledger --days N`: for each stage and for two plain tags
   (manager `overextension_risk`, prior-5-day signed move), show counts passed/rejected, mean residual move of
   each, gate value, split by regime and by manager model. Reuse `decision_attributions` and
   `candidate_price_observations.forward_residual_return`.
   Status: done — `Data/market_db_modules/gate_ledger.py`, CLI `research.py gate-ledger`, tests in
   `tests/test_gate_ledger.py`. Gate value is the proof's (`move_gate_value`: losses avoided minus profits
   sacrificed on rejected decisions). Reads every gate checkpoint (`candidate_snapshots`, incl. unknown verdicts) plus
   `decision_gate_verdicts`, so new gates appear on their own. The move is the raw 5-day residual
   (`forward_raw_residual_return`) signed by the traded side. `decision_attributions` is not reused because it
   stores `forward_residual_return`, signed by the catalyst's direction, which is wrong for a put on a positive
   catalyst; both come from the same `candidate_price_observations` row. Residuals exist only from 2026-09-21, so a
   60-day window has ~170 resolved decisions. Takes about 80 seconds on the full backup.
4. **SPY history gap.** yfinance daily SPY bars stop on 2026-10-15 while `alpaca_iex` has them. Make the
   benchmark read in `Data/market_db_modules/backtesting.py` / `pricing_research.py` fall back across
   providers, and backfill.
   Status: no defect found — `load_histories` already takes, per role, the provider whose series reaches latest
   (yfinance SPY 1d is a one-off load of 501 bars on 2026-10-16; `alpaca_iex` is refreshed daily), and every
   stored residual from 2026-10-05 recomputes identically on the snapshot, so no backfill is needed. Pinned by
   `test_the_benchmark_series_falls_back_to_the_provider_that_still_has_recent_bars` in `tests/test_v2_part4.py`.

## Phase 1 — Entry timing and ranking (gaps 2 and 4)

1. **New shadow gate `ENTRY_EXTENSION`** (`services/gates/entry_extension_gate.py` + policy). Verdict: allow when
   the stock's prior-5-day move in the proposed direction is ≤ 0 **or** the manager's `overextension_risk` is
   low. This is the rule that separated Gemini-era picks (+1.8% vs −1.0% five-day excess). It is separate from
   `CONTINUATION`, which fails every pick on source count and volume and so cannot isolate this effect.
   Status: done (shadow) — `entry_extension_gate.py`, stage `ENTRY_EXTENSION`, key `entry_extension_gate`,
   `ENTRY_EXTENSION_MODE`; inputs are the payload's `company.trend_quality.5d_return_pct` and the manager's
   `overextension_risk`. On 1,095 stored directional decisions: 563 pass, 392 reject, 140 unknown.
2. **Event time and half-life coverage.** `FRESHNESS` returned "missing_or_future_event_time" 2,069 times and
   "unavailable_half_life_prior" 2,782 times (`services/gates/catalyst_gates.py`). Fall back to the earliest
   article time in the catalyst's root when `first_public_time` is missing (`Data/news_db_modules/catalysts.py`),
   and add half-life priors for families without one (`Data/catalyst_models.py`). Most DeepSeek-era candidates
   are family `other`; extend the rules so fewer land there, since Phases 2–3 key on family.
   Status: done — `catalyst-rules-v3` (`Data/catalyst_models.py`): families `corporate_action` and `commentary`,
   half-life defaults for capital return/management/financing/commentary; `first_public_time` falls back to
   article times in the anchor's root with `first_public_time_basis` (`Data/news_db_modules/catalysts.py`; undated
   articles are still not admitted, so on current data it never fires); FRESHNESS reason
   `no_catalyst_articles` split out. Finding: all 2,086 "missing" cases are candidates with zero articles, so no
   time can be recovered for them. `other` 90.1% → 53.9% of FRESHNESS rows (34.8% of the rest is those no-article ones).
3. **Pick score.** A deterministic score from features once they are proven (extension verdict, freshness
   remaining value, relative strength). Log it on each candidate first; later use it to order same-cycle
   candidates in `services/trading/trade_executor.py` when portfolio limits bind. Also log the manager's
   existing `thesis_confidence`/`timing_confidence`/`execution_confidence`, since DeepSeek's overall
   confidence is always "medium".
   Status: logged, not used — `pick-score-v1` (`services/research/pick_score.py`, extension part = the recorded
   `ENTRY_EXTENSION` verdict) attached after that gate and
   stored with the three confidence components as `manager_decision_history` columns (confidence backfilled once)
   and as a `pick_score` candidate snapshot. Candidates stopped before the manager have no side or extension
   verdict, so they get no score;
   `trade_executor.py` does not order by it yet.

## Phase 2 — Market view and short side (gap 1)

1. **Regime gate variants in shadow.** `services/gates/market_regime_gate.py` blocks nearly everything in
   `range` (801 of 913 verdicts). Record one or two alternative policy versions alongside it
   (`decision_gate_verdicts` already keys on `policy_version`), for example "block calls only when SPY's 5- and
   20-day trend are both down", and let Phase 0 show which has positive gate value.
   Status: done (shadow) — correction: `decision_gate_verdicts` keys on `gate_name`, not `policy_version`, so the
   variant has its own stage `REGIME_SPY_TREND` / key `market_regime_spy_trend_gate` (`market_regime_variants.py`,
   always `allowed`); the regime snapshot now carries `spy_return_5d_pct`/`spy_return_20d_pct`. On 1,095 stored
   decisions: 735 pass, 360 reject (production REGIME: 151 pass, 710 reject, 234 unknown).
2. **Use `sizing_multiplier`.** The gate already returns it; nothing reads it. Wire it into order sizing in
   shadow first (log the size it would choose).
   Status: done (shadow) — executor sets `regime_sized_order_qty` on the candidate (so on every execution and the
   `pre_order` snapshot); orders still use the real quantity. The multiplier is 0 or 1 today.
3. **Short side.** 34 candidates with negative earnings or guidance cuts produced no puts. Review how
   `agentCallers/agent_stages/strategist_prompt.py` and `manager_prompt.py` treat bearish catalysts and add a
   new prompt version that states puts on negative company catalysts are in scope. Roll out as a prompt-version
   change and judge it forward through the ledger (no replay).
   *Status: done, not deployed. Snapshot showed no code path blocks puts (strategist puts reach the manager at the same pass rate as calls); cause is prompt-side (`mostly negative` under `do_not_trade`, bullish-only manager technical guidance) plus mixed evidence. New labels `grounded-strategist-v11-bearish-scope`, `grounded-strategist-v5-compact-evidence-bearish-scope`, `manager-prompt-v7-bearish-scope`; manager label now pinned in `tests/test_pipeline_contracts.py`.*

## Phase 3 — Expected move versus option cost (gap 3)

1. `TRADE_ECONOMICS` (`services/gates/trade_economics.py`) already has an enforce path behind a proof;
   `OPTION_HURDLE` is shadow-only by design. Add both to the gate ledger with their "unknown" rate.
   Status: done — both appear in `gate-ledger` with `unknown_rate` (generic over all gate stages).
2. Both need ≥30 past samples per catalyst family (`PricingPolicy.minimum_samples`); Phase 1 step 2 raises that
   coverage. Promote `TRADE_ECONOMICS` through the pooled proof once it qualifies.
   Status: not poolable — TRADE_ECONOMICS is scored on hypothetical option P&L, so it promotes only through a
   sealed frozen-forward `--month all` report (`promote-gate`); pooled proofs cover move-scored gates and
   TRADABILITY's hard-block precision. On the 2026-11-11 backup no option-scored proof can form: 0 of 3,046 tracked
   candidates have a completed counterfactual trade, and TRADE_ECONOMICS has 16 verdicts, all rejections.

## Phase 4 — Tradable list and structure (gap 6)

1. **Tradability pre-check.** Before the strategist call, test whether the default DTE bucket has an eligible
   contract under the premium cap, reusing `agent_helpers/deterministic_option_selector` filters and
   `TradingConfig.resolve_max_premium_per_trade` (`services/core/trading_config.py`). Record a `TRADABILITY`
   rejection in shadow; after confirming it matches the later hard blocks (premium cap, no eligible contract),
   let it skip the strategist and manager calls to save cost.
   Status: done (shadow) — `services/gates/tradability_gate.py`, stage `TRADABILITY`, key `tradability_gate`,
   `TRADABILITY_MODE`; tests the selector's top pick per side (a replay showed "any eligible contract fits" misses
   6/21 cap refusals). Adds about 5 Alpaca requests per candidate (~40 candidates, ~200 requests a cycle) and
   1 account read per cycle; failures are unknown. Proof: pooled hard-block precision ≥ 0.95 with ≥90% of the
   rejected manager trades (call or put) labelled
   (`report-gate TRADABILITY`); once promoted and enforced, a rejection skips the strategist and manager.
2. **Followers on blocked picks.** Follower accounts already paper-trade `debit_spread`, `itm_single`,
   `longer_dte` and `shares` (`services/trading/account_strategies.py`, `follower_fleet.py`) but only copy
   production entries (3–4 closed trades each). Let a follower also take picks production skipped for the
   premium cap, so cheaper structures gather evidence on exactly the picks the cap blocks.
   *Status: implemented (opt-in per account via `ACCOUNT_<NAME>_TAKE_BLOCKED`, `debit_spread`/`shares` only, tagged `entry_kind='premium_cap_blocked'`; they exit on the follower's own rules — `expiry_guard`, `max_hold`, exit policy — never on production's).*
3. **Exit variants on followers.** Add follower exit policies beside `follow:production`: a stock-price stop
   and a partial-profit rule, to test the overnight-gap and trailing-giveback findings without touching
   production exits.
   *Status: implemented (`stock_stop`, `partial_profit` via `ACCOUNT_<NAME>_EXIT_POLICY`); needs a new follower name in `.env`, not new Alpaca keys.*

## Phase 5 — Independent evidence (gap 5)

1. `PROVENANCE` saw one independent root on nearly every candidate. `with_primary_filing` counts the issuer's
   own filing as a second root but needs company evidence on (`COMPANY_EVIDENCE_ENABLED`, `SEC_USER_AGENT`).
   Confirm whether it is enabled in `.env`; if not, enabling it is the main fix.
2. Review root grouping in `Data/news_db_modules/provenance.py` and `config/source.json` so syndicated copies
   collapse but distinct outlets count.
   *Status (5.1/5.2): `.env`/compose are fine (company evidence is stored and covers about 88% of candidates since Nov 9). Bug fixed: `with_primary_filing` counted Yahoo consensus documents as issuer filings (172 of 238 credited roots); pass rate for 357 PROVENANCE verdicts since Nov 9 falls from 147 to 35. Root keys unchanged on purpose (see report); backfill not needed for the gate, it reads documents at gate time. Discovery's `_primary_root` now shares the same `is_issuer_document` predicate (95 existing consensus-rooted `root_events` rows left as they are; new ones root on the filing).*

## Findings from implementation (2026-11-12)

What stands between these checks and a first promotion, measured on the 2026-11-11 backup:

- **Most frozen rows can't be scored.** 2,617 of 3,046 `frozen_forward` rows have no proposed side (catalyst
  direction `unknown` and no manager call/put), so pooled proofs stay starved until those candidates get a direction.
- **Every frozen row is regime `range`.** The two-regime threshold blocks every stage until the market changes
  regime, or the regime label is wrong; check `classify_market_regime` before assuming the market.
- **Residuals start 2026-09-21.** The plan's hand figures (+1.7%, +1.8% vs −1.0%) do not reproduce on the
  residual basis; a raw 5-day excess check gives the same direction but smaller (+0.39% kept vs −0.55% blocked).
- **About 35% of candidates reach FRESHNESS with no catalyst articles** (`no_catalyst_articles`).
- **2,585 company-evidence requests failed** with `provider_collection_failed`; not diagnosed.
- **Pooled proofs bind the evaluator version**: after an evaluator change, re-run `report-gate` before promoting.
- **No counterfactual option trade has completed** (0 of 3,046 tracked candidates: 2,579 `missing_decision`, 451
  `cash`), so option-scored gates (TRADE_ECONOMICS, contract gates) cannot be proven yet by any route.
- **TRADABILITY labels will be scarce.** A rejection counts only once a later stage hard-blocks it or an order goes
  out; "no eligible contract" is recorded only when CONTINUATION allows the pick, so picks that REGIME or
  CONTINUATION stop first stay unlabelled. They count against TRADABILITY's 90% coverage (manager declines
  do not), so its proof may be slow to form.

## Order and code-freeze handling

Ship Phase 0 first. Then land all new shadow verdicts (Phases 1–3) in as few deploys as possible, because a
gate's proof only resets when its own files in `GATE_FILES` change. Promote in the order the ledger supports;
expected first candidates are `ENTRY_EXTENSION` and a regime variant.

## Verification

- Unit tests per `docs/FEATURE_PLAYBOOK.md`: `python3 scripts/project.py test tests.test_<feature>`, including
  `tests/test_bootstrap_steps.py` with the schema snapshot regenerated for any new step.
- Run `gate-ledger` and the pooled report against a copy of the backup. Cross-check against the figures already
  measured by hand: low-overextension picks about +1.7% five-day excess, "prior move ≤ 0 or low" about +1.8%
  kept versus −1.0% blocked.
- Confirm `guard_policy` still refuses `enforce` for a stage without a matching promotion row, and accepts it
  after a pooled promotion on the copy.
- After deploy, check `/api/v2-status` and `scripts/research.py forward-status` show the new verdicts being
  recorded each cycle, with no change in orders placed while everything is in shadow.