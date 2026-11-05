# Plan 03: Delta-first strike ranking

## Context

Each DTE bucket targets a fixed OTM percentage (0.75% to 3.0%). The same 2.5% OTM is a very
different bet on a low-volatility stock than on a high-volatility one. Gemini's numbers are
exaggerated: at 30–45 DTE a KO-like stock lands nearer 0.30Δ than 0.25Δ. But the direction is right:
a fixed % gives an uneven probability of profit across tickers.

The default `hybrid` selector already reads delta. It rejects contracts below 0.25Δ and uses
distance from 0.38Δ, but only as a **tiebreak** after the OTM-% distance.

## Current state

- Mode and tunables: `agentCallers/agent_helpers/deterministic_option_selector/config.py:14-50`
  (`OPTION_SELECTOR_MODE='hybrid'`, `HYBRID_MIN_ABS_DELTA=0.25`, `HYBRID_TARGET_ABS_DELTA=0.38`,
  `HYBRID_REQUIRE_MIN_OTM_DISTANCE=True`).
- Hybrid ranking: `.../hybrid.py:138-153`. OTM distance comes first, delta distance second.
- Shared sort key: `.../filters.py:132-172` (`_basic_sort_key`).
- Bucket OTM targets: `services/options/option_dte_buckets.py` (`target_otm_pct`).
- Follower accounts mirror production entries with alternative contract strategies:
  `services/trading/account_strategies.py:146` (`build_order`) and `services/trading/follower_fleet.py`. They are
  compared on the Followers research page.

## Proposed change

1. Add a selector setting `HYBRID_RANK_BY = otm_pct | delta`, defaulting to `otm_pct` so production
   is unchanged.
2. Under `delta`:
   - the hybrid sort key compares delta distance first, then OTM distance;
   - `HYBRID_REQUIRE_MIN_OTM_DISTANCE` is relaxed to "side-correct OTM", so a 0.42Δ strike nearer
     the money isn't rejected for being under the % floor;
   - a contract without a delta falls back to the current OTM-% ranking, recorded as
     `selection_mode=hybrid_delta_fallback_otm`.
3. Add a per-bucket `target_abs_delta` to `DteBucket` as starting priors, not tuned values:
   12–20: 0.42, 20–30: 0.40, 30–45: 0.38, 45–60: 0.35.
4. **Evaluation via a follower** (no production change): add a strategy `delta_target` to
   `services/trading/account_strategies.py`. For the same production decision and bucket, it picks the
   delta-ranked strike and sizes it like production (one contract, within the premium cap).

## Files to touch

- `.../deterministic_option_selector/config.py`, `hybrid.py`, `filters.py`
- `services/options/option_dte_buckets.py`
- `services/trading/account_strategies.py` (new strategy) and the follower config that names strategies
- `tests/test_deterministic_option_selector*.py` and `tests/test_account_strategies*.py` (extend)

## Risks / open questions

- Alpaca greeks can be stale or missing on thin chains, so measure how often the fallback fires.
- A strike nearer the money costs more premium. With `MAX_PREMIUM_PER_TRADE_DOLLARS=800`, some
  high-priced names will be skipped. Record that skip rate.
- The bucket TP/SL thresholds were set for the current strikes. Higher-delta strikes move less in
  % terms, so the same +70% TP is reached less often. Compare total PnL, not hit rate.
- The option hurdle and trade-economics gates price the production contract. The follower is
  evaluated on its own fills only.

## Tests

- Ranking with greeks: low-IV and high-IV fixtures pick the contract nearest the target delta.
- Missing greeks fall back to the OTM-% order.
- Under `delta`, a liquid strike nearer the money than the OTM target is eligible (spot 120.27, 30_45
  target $3.01, 121C at 0.47Δ). This test was taken out of `tests/test_contract_selector_thin_chains.py`
  when the hard floor was restored in production.
- The default `otm_pct` mode is unchanged (snapshot of current selection on existing fixtures).
- The follower strategy builds a valid single-leg order and skips over the premium cap.

## Verification

`python3 scripts/project.py test` for the selector and account-strategy tests. Run the follower on
paper for at least 30 mirrored entries, then compare total PnL, max drawdown and PnL spread across
tickers on the Followers page.

## Rollout

Change production's `HYBRID_RANK_BY` only after the follower shows a better result over enough
trades.
