# Plan 01: Earnings inside the holding window

> **Status: exit rung built, in shadow (2026-11-04).** No entry gate.

## Context

A long option held through an earnings print takes the IV crush the next morning, even when the
direction is right. Exiting the session before the print keeps the pre-print IV ramp and avoids the
crush, so the mechanism is a forced exit, not an entry block.

## What the data said (backup DB, 2026-11-04)

- **Holds are short.** All 19 closed trades were held 1–6 days: 10 ended at the stop and 9 at the
  trailing stop. The earlier estimate (13–36% of entries hit a print) assumed the maximum hold per bucket
  and was far too high.
- **Step 0 can't be run on past trades.** Yahoo earnings dates have been collected only since 2026-10-28.
  Projecting the stored dates back one quarter suggests about 1 of 19 past trades crossed a print.
- **Coverage is now good.** All 74 symbols the Manager saw since 2026-10-15 have a Yahoo date.

So the entry gate (`EARNINGS_WINDOW`) and the Step 0 go/no-go were dropped. The exit rung is cheap
insurance for the rare trade that would still be open at a print.

## What was built

- **Reader:** `next_earnings_date(symbol, *, as_of, max_age_days=45)` in
  `Data/market_db_modules/company_evidence.py`. It reads `metadata.scheduled_events` from the newest
  `yahoo_consensus` evidence document observed by `as_of` (matched on `metadata.event_key`). It returns the
  first date on or after the New York date of `as_of`, or `None` when there's no document, the document is
  more than 45 days old, or no future date exists.
- **Rung `PRE_EARNINGS`** in `services/options/position_exit_policy.py`:
  - `pre_earnings_due` fires from 15:30 New York time on the last weekday before the print, and at any time
    on or after the print date. Yahoo gives a date only, so it assumes an after-close print and exits the
    session before.
  - It is exempt when the catalyst family is `earnings_*` and the trade was entered on or after the print
    date, i.e. a post-earnings drift trade.
  - It sits in `EXIT_PRIORITY` after `CATALYST_EXPIRED` and before `near_expiration`.
  - It shares the existing `ContractPolicy.exit_mode` with the other V2 rungs: `shadow` records it in
    `shadow_reasons`, and only `enforce` makes it active.
  - The metrics record `next_earnings_date` and `pre_earnings_due`. A reader failure is recorded as
    `inputs.earnings_error` and never breaks the exit context.
- **Strategy hook:** `trading_support/_option_positions_strategy.py` sells in full on an active
  `PRE_EARNINGS`, right after the `CATALYST_EXPIRED` check.
- **Tests:** `tests/test_v2_part5.py` (`PositionRungTests`) covers 15:29 vs 15:30, the print date, a Friday
  before a Monday print, an unknown date, the drift exemption, shadow never active, the strategy action,
  and the reader (newest document, unknown symbol, stale document).

## Known gaps

- The weekday rule ignores exchange holidays (marked `shortcut:` in code). A holiday on the day before a
  print makes the rung fire on the print day itself, which is still before an after-close print but too late
  for a pre-market one. Upgrade to the provider-bar session calendar
  (`Data/market_db_modules/position_outcomes.py:148`) if that ever happens.
- Yahoo dates are estimates (`confirmed: false`) and companies move them. The newest document wins.

## Rollout

Shadow with the other V2 rungs. Enforcement follows `POSITION_EXIT_V2` through `guard_policy` and a
promotion proof. Compare the PnL of would-exit positions after the print with exiting before it.
