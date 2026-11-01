# Plan 04: Portfolio net-direction cap

## Context

The cluster rules (one position per correlated group, at most 25% exposure per cluster) stop
sector concentration. They don't stop the book from being five calls in five unrelated sectors,
which is fully long market beta. A broad sell-off then hits every position at once. A simple
same-direction cap limits that without needing beta or delta data.

## Current state

- Concurrent cap: `services/trading_config.py:187` (`MAX_CONCURRENT_OPTION_POSITIONS=5`).
- Exposure snapshot: `services/trade_executor.py:47-57`, with `cluster_exposure_by_key` and
  `cluster_option_symbols`. The portfolio guardrail is at `services/trade_executor.py:322`
  (`_portfolio_guardrail_reason`).
- Cluster evaluation: `services/risk_clusters.py:6` (`evaluate_cluster`).
- Option side parsing: `services/option_symbols.py`.

## Proposed change

1. Add `open_direction_counts: {'call': n, 'put': n}` to the exposure snapshot. Count positions
   already open plus orders reserved earlier in the same session, so one cycle can't open three
   calls at once.
2. Add a new verdict function in `services/direction_cap_gate.py`, `evaluate_direction_cap`.
   - Inputs: `{'proposed': 'call'|'put', 'counts': ...}` and a `DirectionCapPolicy`, with
     `max_same_direction` (env `MAX_SAME_DIRECTION_OPTION_POSITIONS`, default 4) and
     `mode` (default shadow).
   - Reason codes: `pass_direction_cap` or `same_direction_cap_reached`.
3. Enforce it where the cluster check runs (the session guardrail in `trade_executor`), not as a
   pre-manager gate, because counts change within a cycle.
4. Add the policy to `gate_config` and `GATE_BLOCKS['DIRECTION_CAP'] = 'contracts'`.

## Files to touch

- `services/trade_executor.py`
- `services/direction_cap_gate.py` (new)
- `services/trading_config.py` (env)
- `services/pipeline_version.py` and `services/research_promotion.py`
- `tests/test_direction_cap_gate.py` (new) and `tests/test_trade_executor*.py` (extend)

## Risks / open questions

- In a strong one-way market the cap blocks good trades. Shadow data will show how often, and what
  those blocked trades returned.
- It overlaps with the market-regime gate. If REGIME is promoted later, check whether this cap still
  adds anything.
- A beta-weighted delta cap would be more accurate, but needs per-position beta and greeks. Out of
  scope here.

## Tests

- 4 calls open plus a new call: blocked under enforce, and under shadow `allowed=True` with
  `would_allow=False`.
- 4 calls open plus a new put: passes.
- A within-session reservation counts toward the cap.
- Disabled mode returns no verdict.

## Verification

Unit tests, then one paper cycle in shadow, checking that the verdict is recorded on candidates.

## Rollout

Shadow for at least 4 weeks. Promote only if blocked same-direction entries under-performed.
