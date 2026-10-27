# Phase 5: trade-economics reports and decision finalization

Phase 5 treats the manager's call/put and DTE bucket as a proposal. After the contract
selector picks a contract, a deterministic report prices the side-matching contracts against
dated empirical catalyst scenarios after declared costs. A `TRADE_ECONOMICS` entry gate then
records whether the selected contract is worth buying. A credible thesis can still end as
`no_trade` because the option is unattractive.

Everything is shadow by default. No report grants trading authority, and no new model call is
made: the finalizer is Python, and its explanation is generated from the report.

## Enable and operate

```dotenv
TRADE_ECONOMICS_MODE=shadow                 # off | shadow | enforce
OPTION_RISK_FREE_RATE=0.04                  # Black-Scholes rate, decimal per year (shared with Part 5)
TRADE_ECONOMICS_REPORT_VALIDITY_SECONDS=900 # how long a report can authorize an enforced entry
# Shared with Part 4/5: V2_SCENARIO_IV_CHANGE (-0.05) and V2_EXPECTED_MOVE_MIN_SAMPLES (30)
```

| Mode | Report | Entry gate | Submission recheck |
| --- | --- | --- | --- |
| `off` | Not built | Not attached; legacy eligibility | Not run |
| `shadow` (default) | Built and persisted for every directional result | `allowed` stays true; `would_allow` records the verdict | Recorded as shadow rejections; never blocks |
| `enforce` | As shadow | `allowed = would_allow`; the order builder rejects blocked entries | Blocks submission (`economics_recheck_failed`) |

`enforce` is promotion-guarded like the other V2 gates. `TradeEconomicsPolicy.from_env()` falls
back to shadow unless `research_gate_promotions` has a `TRADE_ECONOMICS` proof for the current
policy and source hash. The proof covers every numeric assumption in the policy, but not the mode.
Setting the variable alone does not enforce.

Inspect reports without network or model access:

```bash
python3 scripts/research.py --db /path/to/market.db economics 'economics:HASH'
python3 scripts/research.py --db /path/to/market.db economics-report --days 7
```

`economics-report` counts outcomes, coverage and selected-contract statuses. It also lists
`TRADE_ECONOMICS` (decision time) and `TRADE_ECONOMICS_SUBMISSION` rejections, separated into
shadow and actual blocks. For each rejection reason it counts how many candidates were submitted
anyway. Expected P&L figures are decision-time estimates, not realized outcomes or promotion evidence.

## Where it runs

`_apply_post_manager_gates` in `agentCallers/decision_pipeline.py` runs, in order: the option hurdle, scenario
inputs, the deterministic selector, the regime gate, the evidence-thesis gate and then
**trade economics**. Decision history and the streaming execution callback run after it. The
callback therefore only ever receives finalized results, and the batch path carries the same gate
through `selected_options`. The policy is read once per cycle.

The gate is registered in `services/entry_gates.py`, so order candidates, decision history,
selected-options output, candidate snapshots and Part 6 research inputs carry it automatically.

## The report

`services/trade_economics.py` builds one report per directional result.

**Contracts.** It prices every side-matching contract in the bucket-refreshed chain (at most 40),
always including the selected one. Rows mark `selected`. The report never chooses a different
contract; the selector owns selection.

**Holding horizon (per contract).** The holding period is the contract's DTE minus the forced-exit
DTE of the manager's `target_dte_bucket`. When the manager gave `none`, the bucket that contains
the contract's DTE is used. Empirical scenarios only exist at the stored response horizons
(1, 3, 5, 10, 21, 45 days), so the period is snapped *down* to the largest one. Every scenario
exits at that horizon. For example, a 25-DTE contract in `20_30` (forced exit at 12 DTE) holds
13 days and is evaluated at 10.

**Scenarios.** The scenarios are the total stock returns (`stock_return`) of past catalysts in the
same `catalyst_family` at that horizon. They use the availability rules of the existing
distribution: outcome, recording and registration all at or before the report time, and the
candidate's own catalyst excluded. Total returns include each past period's realized market and
sector moves. The residual-only result is kept as a labelled sensitivity. Rows written without
a total return are excluded and counted, not replaced by the residual.

**Independence.** Catalysts on the same event day, or on the same ticker within the horizon,
form one cluster. Each cluster gets equal weight, shared across its catalysts. The effective
sample size is the number of clusters. Expected values, quantiles and the break-even price are
reported only when it reaches `V2_EXPECTED_MOVE_MIN_SAMPLES`. Otherwise the report keeps the
unweighted scenario range and the stress grid, and marks expected value unavailable.

**Pricing.** Each scenario is repriced with European Black-Scholes:

- The IV is calibrated to the quote mid by bisection. Alpaca's IV is recorded for comparison only.
- Every scenario applies the declared IV change (`V2_SCENARIO_IV_CHANGE`, −5 vol points).
- Entry pays the ask plus one fee ($0.65).
- Exit receives the model value less the entry half-spread, minus one fee.
- An exit worth no more than its fee is held to expiry and assumed worthless. This keeps every
  loss within the premium plus the entry fee.
- Crossing the quoted spread is the whole execution cost; no measured slippage is added.

**Unavailable rows.** A row that cannot be priced records why:

| Status | Meaning |
| --- | --- |
| `invalid_quote` / `stale_quote` | No usable bid/ask, or a quote older than 900 s at report time |
| `missing_contract_inputs` | Missing strike, expiry or spot, or the wrong side |
| `no_holding_rule` / `holding_period_too_short` | No bucket applies, or the hold is under one day |
| `unsupported_exercise_assumption` | More than 10% in the money, where early exercise matters |
| `iv_calibration_failed` / `invalid_iv_scenario` | No volatility reproduces the mid, or the IV shift makes it non-positive |
| `no_scenarios` | No stored scenarios for the family and horizon (or no catalyst family) |

**Row outputs.**

| Output | Contents |
| --- | --- |
| `costs` | Entry/exit half-spread, round-trip fees, time decay and the IV-change effect at an unchanged spot, and zero slippage |
| `outcomes` | Capital at risk, expected net P&L and return, weighted quantiles (p05–p95), the weight of profitable scenarios, break-even entry price, residual-only expected P&L, and the unweighted scenario range |
| `stress` | Unweighted IV-implied moves (−2σ…+2σ) × IV shifts (−10…+5 points); no probability attached |

**Verdict.**

| Outcome | Reason code | When |
| --- | --- | --- |
| `trade` | — | Expected net return above `minimum_expected_net_return` (0) |
| `no_trade` | `economics_expected_return_below_minimum` | Expected net return at or below the minimum |
| `unavailable` | `economics_no_selected_contract` | No contract was selected |
| `unavailable` | `economics_selected_contract_unavailable` | The selected contract could not be priced |
| `unavailable` | `economics_expected_value_unavailable` | Too few independent clusters |

Missing scenarios can never produce a positive expectation.

## Gate and finalization

`trade_economics_gate` carries:

- `allowed`, `would_allow`, `proposed_decision` and `eligible_decision` (`neither` when blocked)
- `reason_codes`, `report_id`, `package_id`, `valid_until`, `selected_option_symbol` and `outcome`
- the key figures under `evidence`
- a one-sentence `summary`, which is the finalizer's explanation

Two more reasons block:

- `economics_report_unpersisted`: the report could not be stored durably.
- `economics_check_error`: the report raised. The error becomes a blocking verdict rather than a
  missing one, because a missing optional gate leaves eligibility unchanged.

`record_gate` stores the verdict as the `TRADE_ECONOMICS` candidate snapshot. Would-block reasons
become rejections with `shadow=1` unless the gate actually blocked.

## Submission recheck

Before a broker order, `OptionTradeExecutor` calls `recheck_trade_economics` with the fresh arrival
snapshot. It runs after the liquidity check and before the limit-entry shadow plan. It reloads
the stored report and the policy the report was built under. It then checks:

| Reason code | Check |
| --- | --- |
| `economics_report_missing` | The report is persisted and readable. An enforced candidate without a gate is also blocked. |
| `economics_contract_mismatch` | The order's contract is the report's selected contract |
| `economics_report_expired` | Submission is before `valid_until` |
| `economics_underlying_stale` | The underlying quote is fresh (≤ 5 s) |
| `economics_underlying_moved` | The underlying moved at most 0.5 ATR since the report. Skipped and labelled `atr_unavailable` when ATR is unknown. |
| `economics_event_revised` / `economics_event_unverifiable` | The catalyst's family, direction, latest root-event time and independent root count are unchanged since the report |
| `economics_quote_stale` / `economics_quote_invalid` / `economics_expected_value_unavailable` / `economics_edge_exhausted` | The selected contract, repriced on the frozen scenarios with the arrival quote (≤ 5 s) and arrival spot, still has positive expected return |

The verdict is checkpointed as `TRADE_ECONOMICS_SUBMISSION`. Its reasons are rejections, shadow
unless enforced. In enforce mode a failed recheck returns `portfolio_guardrail='economics_recheck_failed'`
and never reaches the broker.

## Persistence

`candidate_economics_reports` (`Data/market_db_modules/trade_economics*.{py,sql}`) is additive,
immutable and hash-checked. Each report's identity is its content hash (`economics:<sha256>`).
A report must match a recorded candidate. An optional `package_id` must belong to the same
company and predate the report.

Each report freezes the inputs needed for replay without network access:

- contract inputs and quotes
- spot and ATR
- every scenario's catalyst ID, total and residual return, cluster and weight
- the catalyst revision
- the full policy assumptions and model description
- limitations and coverage

Phase 0 had planned a `trade_economics_reports` table that required an evidence package, which most
legacy-route candidates have none of while evidence collection is off by default. Nothing ever wrote
to it. It and its contract are removed; migration drops the table from existing databases only
while it is empty.

## Design decisions

- **Deterministic finalizer, no second model call.** The manager already judged tradability; an
  extra call would double cost and could only restate the report. The explanation comes from the
  report, so it cannot claim figures the report does not contain.
- **Total stock returns.** Options pay on total moves; the existing residual-only model assumed
  zero systematic movement. Past total returns carry the market of their own periods, which the
  report states as a limitation.
- **Horizon from the manager's DTE bucket.** The forced-exit rule is the documented longest hold,
  matching `thesis_capture`. It is snapped down so the report never credits moves beyond the hold.
- **Black-Scholes repricing, shared with Part 5.** Calibrating IV to the mid makes the model
  reproduce the entry price exactly. Large moves and convexity are handled by repricing, not a
  Taylor expansion. `services/option_pricing.CalibratedOption` prices both this report and the
  Part 5 scenario scorer.
- **Ask/bid is the execution cost.** Measured market-order slippage is not added. It remains
  visible in the Part 5 execution baseline.
- **Guarded enforce mode.** The enforce path and recheck are complete and tested but inert until
  Phase 7 evidence supports a proof.
- **The live limit-entry ladder is out of scope.** Entries remain market orders. The report's
  assumptions state that no maximum entry price is enforced at the broker.

## Limitations

- Path-dependent exits (stop loss, take profit, trailing stop, scale-outs, thesis exits) are not
  modelled; every scenario exits at the holding horizon.
- Scenarios are stratified by family and horizon, not by market regime.
- One IV change applies to every scenario; IV is not modelled jointly with the size of the move.
- European exercise, no dividends; deep in-the-money contracts are refused rather than approximated.
- Independence clustering is conservative but heuristic: events on different days and tickers
  are treated as independent even when sector-wide.
- In batch mode orders execute after every company finishes. A long cycle can outlive the
  900-second validity, which matters only under enforcement.

## Existing behaviour found during implementation

These are pre-existing choices that affect how far the report can be trusted. Phase 5 itself did
not change them; items 1 and 3 were fixed in a follow-up.

1. **Fixed: live exit rules re-resolved from the current DTE.**
   - Each bucket's forced-exit DTE (8/12/18/27) is below that bucket's own minimum, so positions
     were re-bucketed before their forced exit could fire. Below 12 DTE the default 1-day rule
     applied, and take-profit, stop-loss and trailing giveback tightened as positions aged.
   - The whole exit rule is now chosen once, from `entry_dte` in position state (the DTE when the
     option manager first saw the position), so the entry bucket's forced exit fires.
   - Positions open at deployment use their recorded `entry_dte`. One already past its entry
     bucket's forced-exit DTE is sold on the next management cycle.
   - `thesis_capture` now uses entry DTE minus the forced-exit buffer as the longest hold.
2. **Fixed: the Part 5 Greek approximation turned a crash into a profit.**
   - `score_contract` applied an unbounded quadratic gamma term. For a call with gamma 0.04, a −60%
     move added +$72 per share, so a worthless option scored as a large gain. Any drop beyond
     2·delta/gamma (24% here) counted as favourable.
   - The scorer now reprices each scenario with the same calibrated Black-Scholes model as this report,
     so Greeks are no longer inputs. It feeds the hybrid selector's ranking (`V2_SCENARIO_MODE`) and
     the limit-entry price ceiling.
   - `ContractPolicy` is now `contract-execution-v3-black-scholes`, so Part 6 runs frozen under the old
     scorer ask for a new challenger instead of replaying under the new one.
3. **Fixed: the manager decided on a chain it could not trade.**
   - The default window was 1–8 DTE while every target bucket is 12–60 DTE. Its expirations, IV
     summary and IV percentile described contracts it could never select. Its "longer-term" IV
     (20–45 DTE) was always empty.
   - The window is now derived from the buckets (12–60 DTE). Alpaca contract pages are followed
     (up to 10) so far expirations are not cut at the 500-contract page.
   - A call or put with `target_dte_bucket` `none` now gets the strategist's bucket or the default,
     instead of trading 3–10 DTE contracts that no exit bucket covers.
   - The short-dated (≤10 DTE) IV used for the event-pricing warning is now outside the window.
4. **Several horizons coexist.** Each is used for a different purpose:
   - the catalyst family's `expected_horizon` (hurdle gate and Part 5 scenario selector)
   - the manager's `target_dte_bucket` (contracts and this report)
   - the evidence thesis `minimum_days..maximum_days`
   - `thesis_capture`'s bucket-derived horizon
5. **The Phase 0 economics table required an evidence package** that most candidates cannot have.
6. **Spot prefers the last trade.** `_get_reference_stock_price` uses `latest_trade_price` before
   the quote midpoint, so a stale print drives selection, the hurdle and this report.
7. **Optional entry gates allow when missing.** An enforced optional gate that failed to attach
   would allow entry. Phase 5 avoids this with error verdicts and an executor safety net; the
   evidence-thesis gate relies on its evaluation not raising.
8. **Part 6 infers gates from snapshot shape.** `research_inputs.load_inputs` treats any candidate
   snapshot containing `would_allow` as a gate. Both economics snapshots become Part 6 gates this
   way, without being registered anywhere.
9. **The arrival underlying `price` is the stock ask.** `_get_latest_stock_price` reports the ask
   as `price`, and `limit_entry` measures underlying movement with it. The economics recheck uses
   `mid_price` when present.

## Code and validation

| Concern | Location |
| --- | --- |
| Policy and promotion guard | `Python Scripts/services/trade_economics_policy.py` |
| Black-Scholes and IV calibration | `Python Scripts/services/option_pricing.py` |
| Report, gate, finalizer explanation, recheck | `Python Scripts/services/trade_economics.py` |
| Scenario query | `Data/market_db_modules/pricing_research.py` (`catalyst_return_scenarios`) |
| Catalyst revision | `Data/news_db_modules/catalysts.py` (`get_catalyst_revision`) |
| Storage and summary | `Data/market_db_modules/trade_economics.py`, `trade_economics_schema.sql` |
| Integration | `agentCallers/decision_pipeline.py`, `services/entry_gates.py`, `services/trade_executor.py`, `agent_contracts.py` |
| Tests | `tests/test_trade_economics.py` |

```bash
DB_PATH=/tmp/phase5.db TELEMETRY_ENABLED=false PYTHONPATH=tests:. \
  python3 -m unittest tests.test_trade_economics
```

The 52 Phase 5 tests use isolated databases and fixed clocks, with no model, provider or broker
calls. They cover the plan's acceptance cases:

- A credible bullish thesis ends `no_trade` when the option is unattractive.
- Expired, stale, moved, revised and edge-exhausted reports cannot authorize an enforced entry.
- Missing or dependent scenarios cannot produce an expected value.
- Adverse scenarios for calls and puts stop exactly at the premium plus fee.

They also cover Black-Scholes parity, IV and quote round trips, crash repricing, cluster weights,
holding rules, unsupported exercise, immutability, unpersisted and failed reports,
promotion guarding, order eligibility, executor blocking, and finalization before the streaming
callback.
