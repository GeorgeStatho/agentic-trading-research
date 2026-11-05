# Plan 02: Market-hours catalyst age

## Context

The FRESHNESS gate decays a catalyst's remaining information value by the calendar minutes since
`first_public_time`. News published Friday at 16:05 New York time is about 65 hours old by Monday's
open, although the market has had no session to react. With the `analyst_action` half-life of
1440 minutes, the gate counts that news as mostly decayed before any trading has happened.

The other half of Gemini's idea, price drift since first print, **already exists**. The
`PRICE_ABSORPTION` gate (`services/research/price_response.py:157`) measures the beta-adjusted residual move
from the last completed close before publication, so overnight gaps are included. This plan does
not touch it.

## Current state

- `Data/catalyst_models.py:24-27`: `HALF_LIFE_MINUTES` per family, with
  `DECAY_BASIS = 'elapsed_calendar_minutes'`.
- `services/gates/catalyst_gates.py:53-58`: `age` and `effective_age` are wall-clock minutes, and
  `recency_score(effective_age/1440, half_life_days=...)`.
- Session helpers: `services/research/price_response.py:21` (`completed_at`), and New York time handling in
  `services/evaluation/evaluation_clock.py`. Check whether it already has a session calendar before writing one.

## Proposed change

1. Add `market_minutes_between(start, end)`: regular-session minutes (09:30–16:00 New York time),
   skipping weekends and NYSE holidays.
   - Reuse an existing holiday list if the repo has one (grep `holiday`); otherwise add a small,
     static, versioned list.
2. Pre- and post-market publications count from the next open, so a pre-market print at 07:30 has
   age 0 at 09:30.
3. `CatalystGatePolicy` gains `decay_basis: 'calendar' | 'market'`, default `calendar` so behaviour
   is unchanged, with the version bumped to `catalyst-gates-v2`.
   - Under `market`, `age` and `effective_age` use market minutes. `HALF_LIFE_MINUTES` stays as is,
     but note in code that the priors were set on calendar minutes, so recalibrate later.
4. Always record both ages in the FRESHNESS metrics (`catalyst_age_minutes` and
   `catalyst_market_age_minutes`), whichever basis is active, so the research layer can compare them.

## Files to touch

- `services/gates/catalyst_gates.py`
- `Data/catalyst_models.py` (make `DECAY_BASIS` policy-driven, or add a second constant)
- `services/core/pipeline_version.py` (the new policy field is picked up through `CatalystGatePolicy`)
- A small session-minutes helper, wherever `evaluation_clock.py` keeps New York time logic
- `tests/test_catalyst_gates.py` (extend)

## Risks / open questions

- The half-life priors were tuned on calendar time. Switching basis without recalibrating makes
  every catalyst look fresher. That's why the default stays `calendar`, and FRESHNESS is shadow
  anyway.
- Overnight news on a busy stock *is* partly priced pre-market. `PRICE_ABSORPTION` already covers
  that, so the two gates complement each other.

## Tests

- Friday 16:05 → Monday 09:35 gives 5 market minutes.
- A Wednesday pre-market print at 07:30, evaluated at 09:30, gives 0.
- A holiday Monday.
- A half-day close, if the calendar supports it; otherwise document it as a known gap.
- The default `calendar` basis is byte-for-byte unchanged.

## Verification

`python3 scripts/project.py test tests.test_catalyst_gates`. Then a read-only replay of the last 30
days of FRESHNESS snapshots from the backup DB, counting how many verdicts would flip under `market`.

## Rollout

Record both ages in shadow. Promote `market` only if flipped verdicts show better forward outcomes
in the research layer.
