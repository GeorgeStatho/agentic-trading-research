# Plan C: separate evidence quality from forecast quality (decision attribution)

Status: proposed (2026-10-27). See [README.md](README.md) for the index.

## Context
FinanceGym's paired rubrics score two things separately:
- **Pre-cutoff:** did the agent find and use the evidence?
- **Post-cutoff:** did its forecast hold?

The main finding is the gap between them: roughly 46–50% versus 10–13% for every system.

This project scores decisions only by the sign of the option P&L (`resolved_outcome_label`, set
in [`services/options/position_manager.py:54`](../../Python%20Scripts/services/options/position_manager.py#L54) and [`services/trading/outcome_reconciliation.py:116`](../../Python%20Scripts/services/trading/outcome_reconciliation.py#L116)). A loss
cannot be traced to a cause. It could be:
- **bad evidence:** an ungrounded or stale thesis;
- **a wrong call on good evidence:** the underlying moved the other way;
- **the option failing to capture a right call:** premium, theta or IV.

**Data reality on the server backup:**
- 2,774 manager decisions: 971 call, 95 put, 1,708 neither.
- Only 29 have `realized_pnl_pct`.
- Only 6 `candidate_price_observations` rows have `forward_residual_return`.
- Only 1 production decision joins an `evidence_thesis_checks` row. The Phase 4 legacy-route
  check has barely run in production.
- Every strategist recommendation carries `evidence_claims` (with `evidence_article_ids`), plus
  `evidence_quality`, `contradictions_present`, `setup_quality` and `timing_clarity`, which are
  the model's self-ratings.

The forecast layer must therefore use underlying price moves from daily closes, which works for
every directional decision. The evidence layer needs a deterministic grounding proxy wherever the
Phase 4 check is absent.

**Outcome.** Each decision gets three labels: evidence, direction and expression. A report and the
Decision Autopsy show the evidence × direction matrix, and show whether the strategist's own
`evidence_quality` rating predicts anything. This is monitoring only and grants no authority.

## Changes

### 1. Pure scoring: `Python Scripts/services/evaluation/decision_attribution.py`
`ATTRIBUTION_VERSION = 'decision-attribution-v2'`. All functions are pure and get their data
passed in.

**`evidence_layer(strategist, thesis_check, catalysts, articles, decided_at) -> dict`**

Returns `{quality, source, signals}`, with `quality ∈ {strong, adequate, weak, unchecked}`.

When a Phase 4 check exists (`source='thesis_check'`), the check decides:

| Quality | Condition |
|---|---|
| `strong` | status `validated`, no failed central claims |
| `adequate` | `validated_with_warnings` |
| `weak` | `deferred` / `invalid_output`, or any `central_claim_failed` / `package_opposing_evidence_unaddressed` |

Otherwise (`source='grounding_proxy'`), compute deterministic signals from the frozen manager
input:
- `cited_articles`: unique `evidence_article_ids` across the claims.
- `cited_articles_resolvable`: share of those IDs that exist in the input's supporting articles
  and were published at or before `decided_at`.
- `catalyst_claim_present`: whether a `claim_type='catalyst'` claim exists.
- `independent_roots` and `primary_source_present`: from the primary catalyst state in
  `candidate_catalysts`.
- `freshest_evidence_age_hours`: same method as `news_lag_report`.

The proxy maps to quality as follows, with thresholds as named constants:

| Quality | Condition |
|---|---|
| `strong` | catalyst claim, resolvable ≥ 0.9, and (roots ≥ 2 or a primary source), fresh ≤ 72 h |
| `weak` | no catalyst claim, or resolvable < 0.5, or no cited articles |
| `adequate` | everything else |

`unchecked` means the inputs are missing.

The model's self-ratings (`evidence_quality`, `contradictions_present`) are copied into
`signals.model_self_rating` and never used to compute `quality`.

**`direction_layer(decision, payload, closes) -> dict`**
- Reuse `llm_direction_score.forward_returns` (5 and 10 trading days from the frozen reference
  price), keeping its `HORIZONS`.
- For `call`/`put`, use the signed return. `correct` means > +0.5%, `wrong` means < −0.5%, and
  anything between is `flat`. The ±0.5% band is a named constant.
- `neither` / `do_not_buy` gives `abstained`, with `abs_move` recorded so missed moves are
  visible.
- Missing closes give `pending` while the horizon has not elapsed, and `unavailable` after it
  has. An `unavailable` horizon is re-checked for `RECHECK_DAYS` (30) past the longest horizon's
  window, so a price-fetch outage does not settle a call for good; `record.settled` is false until then.
- When `forward_residual_return` exists, also record it as a beta-adjusted cross-check.
- The primary horizon is 5 days. Both are stored.

**`expression_layer(decision_row, direction) -> dict`**
- Only for executed trades (`realized_pnl_pct` or a closed `latest_trade_pnl_pct`).
- `captured`: direction correct and P&L > 0.
- `lost_despite_direction`: direction correct and P&L ≤ 0.
- `lost_with_direction`: direction wrong.
- `lost_in_flat`: direction flat and P&L < 0 (premium decay on a move inside the band).
- `no_direction_signal`: traded, but the direction is flat (P&L ≥ 0), unavailable or abstained.
- `pending` while the P&L is unknown. Past the re-check window, a trade that is not a known open
  position (`open_*`) and still has no P&L becomes `unavailable`.
- Untraded decisions are `not_traded`.

**`attribute(...) -> dict`** combines the three layers and adds a one-word
`primary_failure ∈ {evidence, forecast, expression, none, pending}`, using this precedence:
1. weak evidence plus a wrong direction gives `evidence`;
2. adequate or strong evidence plus a wrong direction gives `forecast`;
3. a correct direction plus a lost trade, or a flat direction plus a losing trade, gives `expression`.

### 2. Persistence ([`Data/market_schema.sql`](../../Data/market_schema.sql), plus `Data/market_db_modules/decision_attribution.py`)
```sql
CREATE TABLE IF NOT EXISTS decision_attributions (
  manager_decision_history_id INTEGER PRIMARY KEY REFERENCES manager_decision_history(id),
  attribution_version TEXT NOT NULL, evidence_quality TEXT NOT NULL, evidence_source TEXT NOT NULL,
  direction_outcome TEXT NOT NULL, expression_outcome TEXT NOT NULL, primary_failure TEXT NOT NULL,
  record_json TEXT NOT NULL, computed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_decision_attributions_failure ON decision_attributions(primary_failure);
```
Functions:
- `upsert_attribution(record, *, db_path=None)`. A row is replaced only while it is `pending`, has
  `settled = false`, or is on an older `attribution_version`. Settled rows are left alone.
- `list_attributions(*, since=None, db_path=None)`
- `decisions_needing_attribution(limit, *, db_path=None)`: decisions with no row first (oldest
  first), then unsettled or stale-version rows, least recently computed first, so re-checks never
  starve new decisions. It joins:
  - `manager_decision_payloads` (frozen input/output, as `llm_replay_store` does);
  - `evidence_thesis_checks` on `candidate_id`;
  - the primary `candidate_catalysts` row.

### 3. Runner and cycle
`services/evaluation/decision_attribution_runner.py: attribute_decisions(*, limit=500, db_path=None)`:
- Loads closes with `market_db_modules.backtesting.daily_closes_after`, writes rows, and returns
  counts.
- Hook it into [`Python Scripts/news_collector_main.py`](../../Python%20Scripts/news_collector_main.py)`:run_news_collection_cycle` after
  `collect_catalyst_responses`:
  `result['decision_attribution'] = observe(attribute_decisions)`.
- It needs no model and no network. Daily closes come from the collector's existing refresh.
  Missing symbols stay `pending` or `unavailable`.

### 4. Report CLI
Add an `attribution` subcommand to [`scripts/research.py`](../../scripts/research.py): `--days N` (0 means all) and
`--horizon 5|10`. It is read-only JSON, like `thesis-report`, and contains:
- `matrix`: evidence_quality × direction_outcome, with count, hit rate and mean signed return
  for each cell.
- `by_evidence_source`: `thesis_check` vs `grounding_proxy`.
- `model_self_rating_calibration`: hit rate by the strategist's own `evidence_quality` value.
  This shows whether the self-rating carries signal.
- `primary_failure_counts`.
- `expression`, for traded decisions only: every traded outcome above, `pending` and `unavailable` included.
- `abstentions`: the distribution of `abs_move` for `neither`, against directional decisions.
- Bootstrap standard errors (n=1000, seeded) on hit rates. Put the helper in
  `services/evaluation/decision_attribution.py` so plan B can reuse it.

### 5. Decision Autopsy surface
- `api_support/decision_autopsy._serialize_ledger_row` (:75): left-join
  `decision_attributions` in the ledger query and add
  `"attribution": {evidence_quality, evidence_source, direction_outcome, expression_outcome,
  primary_failure, horizon_returns}`, or `null`.
- `web_dashboard/src/decisionAutopsy.tsx` (now [`decisionsPage.tsx`](../../web_dashboard/src/decisionsPage.tsx) / [`decisions.ts`](../../web_dashboard/src/decisions.ts) after the dashboard redesign): add a small "Attribution" row with three
  chips: evidence, direction and expression. Use the existing chip and label styles, plus
  `formatLabel` from `analyzedCompanyNews.tsx`.
- Add a fixture to Demo Mode so `?demo=1` renders it.

### 6. Docs
- Add an "Attribution" section to [`docs/RESEARCH_GUIDE.md`](../../docs/RESEARCH_GUIDE.md), under "Watching it", with the
  command.
- Add the module to [`docs/CODE_MAP.md`](../../docs/CODE_MAP.md).
- Add "evidence quality", "direction outcome" and "primary failure" to [`GLOSSARY.md`](../../GLOSSARY.md).

## Tests
`tests/test_decision_attribution.py`. Use `tests/db_fixtures.temporary_database`, plus
`DatabaseFixture.add_candidate`/`add_closes` from [`tests/test_llm_backtest.py:39-60`](../../tests/test_llm_backtest.py#L39-L60).

- **Evidence layer:** a table of Phase 4 check records maps to quality levels. Proxy
  thresholds produce strong, adequate and weak. An article published after `decided_at`
  lowers resolvability. Self-ratings never change `quality`.
- **Direction layer:** synthetic closes give correct, wrong and flat at 5 and 10 days. Too few
  closes gives `pending` before the horizon and `unavailable` after it. `neither` gives
  `abstained` with `abs_move`.
- **Expression layer and `primary_failure`:** each branch of the precedence.
- **Persistence:** pending rows are recomputed, settled rows are untouched, and a version bump
  recomputes.
- **Report:** matrix totals equal the row count, and the bootstrap output is deterministic
  under the seed.
- **API:** the serializer includes `attribution`, and gives null when no row exists
  (extend the existing autopsy API test).
- **Dashboard:** `python3 scripts/project.py dashboard build` succeeds.

## Verification
1. Run `python3 scripts/project.py test` and `python3 scripts/project.py dashboard build`.
2. On a throwaway copy of the live database backup, run `attribute_decisions(limit=5000)` and then
   [`scripts/research.py`](../../scripts/research.py)` --db <copy> attribution --days 0`.
3. Expect about 1,066 directional decisions scored where closes exist, and 1,708 abstentions.
4. Check that matrix totals reconcile with `manager_decision_history`. Eyeball 10 autopsies in
   the dashboard against their charts.
