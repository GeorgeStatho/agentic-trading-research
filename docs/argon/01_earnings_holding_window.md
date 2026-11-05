# Plan 01: Earnings inside the holding window

## Context

Long single-leg options held through an earnings print take the IV crush the next morning, even
when the direction is right. Nothing in the pipeline knows when a company reports. Gemini proposed
blocking entries when earnings are 5 days away or fewer. That is the wrong window:

- What matters is whether the print falls before the position's **forced exit**, not before expiry.
- The maximum hold per bucket is entry DTE minus forced-exit DTE: about 12, 18, 27 and 33 calendar
  days for 12–20, 20–30, 30–45 and 45–60. With earnings roughly every 91 days, a random entry hits a
  print about 13 / 20 / 30 / 36% of the time.
- A news-driven pipeline probably sees pre-earnings buzz (previews, analyst moves) more often than a
  random entry would, so the real rate may be higher. Measure it before building anything.
- The pre-earnings IV ramp *helps* a long option. Exiting the session before the print keeps that
  gain and avoids the crush, so a forced exit is better than an entry block.

## Current state

- Earnings date: `Webscraping/company_evidence/yahoo_consensus.py:64` writes `Earnings Date` into a
  consensus evidence document. Nothing reads it for trading.
- Evidence store readers: `Data/market_db_modules/company_evidence.py:85-93`
  (`get_evidence_document`, `get_company_fact`, `latest_evidence_package`).
- Forced-exit DTE per bucket: `services/options/option_dte_buckets.py` (`force_exit_days_to_expiration`).
- Exit rungs and priority: `services/options/position_exit_policy.py:10` (`EXIT_PRIORITY`).
- The catalyst taxonomy already has `earnings` and the families `earnings_positive` and
  `earnings_negative` (`Data/catalyst_models.py:12,18`).

## Step 0: measure first (read-only, no paid calls)

Use the server backup `~/stock-restore/db-dev/stock_experiment.db`, opened read-only with
`?immutable=1`. Never use `get_connection` on it.

1. Load closed option trades (`position_outcomes` / `position_theses`) with open and close timestamps.
2. Load each underlying's earnings dates from the Yahoo consensus `evidence_documents`. Keep every
   distinct date observed, and take the one valid at entry time.
3. Report:
   - the % of trades whose open→close interval contained a print;
   - the median PnL % of those trades vs the rest, and their win rate;
   - the % of entries with an unknown earnings date.

**Go / no-go:** continue only if at least ~10% of trades crossed a print, or those trades lost
clearly more. Otherwise record the result in this file and stop.

## Proposed change

1. **Reader:** add `next_earnings_date(company_id, *, as_of, db_path=None)` in
   `Data/market_db_modules/company_evidence.py`. It returns `{date, source, observed_at}`, or `None`
   when unknown or stale (older than 45 days).
2. **Exit rung `PRE_EARNINGS`** (preferred mechanism) in `services/options/position_exit_policy.py`:
   - fires at or after 15:30 New York time on the last regular session before the print;
   - a pre-market print means the session before; an after-close print means the same day. Yahoo
     only gives a date, so assume after-close for safety and exit the session before;
   - position in `EXIT_PRIORITY`: after `stop_loss` and `thesis_invalidated`, before `near_expiration`;
   - policy `EARNINGS_EXIT_MODE=shadow|enforce|disabled`, default shadow. In shadow it records the
     exit it *would* take.
3. **Optional entry gate `EARNINGS_WINDOW`** in `services/gates/entry_gates.py`:
   - blocks only when the print is fewer than `EARNINGS_MIN_SESSIONS_BEFORE_ENTRY` (default 2)
     sessions away, i.e. too close for the pre-print IV ramp to pay for the spread;
   - shadow by default, with reason codes `earnings_too_close`, `earnings_date_unknown` and
     `pass_earnings_outside_window`.
4. **Exemption:** when the trade's own catalyst family is `earnings_*` and its `first_public_time`
   is after the last print, it is a post-earnings drift trade and both mechanisms pass it.
5. **Unknown date:** pass with reason `earnings_date_unknown`, so the hit rate stays measurable.

## Files to touch

- `Data/market_db_modules/company_evidence.py` (reader)
- `services/options/position_exit_policy.py` (rung)
- `services/gates/entry_gates.py` plus a new `services/gates/earnings_window_gate.py` (gate)
- `services/core/pipeline_version.py` (`gate_config`) and `services/research/research_promotion.py` (`GATE_BLOCKS`)
- `tests/test_earnings_window.py` (new)

## Risks / open questions

- Yahoo dates are sometimes estimates, and companies move them. Store `observed_at` and prefer the
  newest observation.
- Coverage: only companies the evidence collector has visited have a date. The unknown rate from
  Step 0 decides whether a second source is needed.
- Same-day after-close prints and half-days need explicit tests.

## Tests

- Each bucket edge: a print one day inside and one day outside the forced-exit window.
- Pre-market vs after-close (date-only) handling.
- Unknown and stale dates.
- The post-earnings exemption.
- Rung priority vs `stop_loss` and `near_expiration`.
- Shadow mode never changes `allowed`.

## Verification

`python3 scripts/project.py test tests.test_earnings_window`, then one dry-run `option_manager`
cycle on paper, checking that the shadow rung is recorded on open positions.

## Rollout

Shadow for at least 4 weeks. Compare the PnL of would-exit positions after the print vs exiting
before it. Enforce through `guard_policy` and a promotion proof.
