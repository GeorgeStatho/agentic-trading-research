# Option Trailing Profit And Momentum Guide

This guide explains how the current option exit engine manages open positions with:

- static stop-loss and expiration exits
- trailing-profit protection
- partial scale-outs
- momentum-history exits

The implementation lives primarily in:

- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)
- [Python Scripts/trading_support/_option_positions_strategy.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/_option_positions_strategy.py>)
- [Python Scripts/trading_support/_option_positions_snapshot.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/_option_positions_snapshot.py>)
- [Python Scripts/trading_support/_option_positions_state.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/_option_positions_state.py>)
- [Python Scripts/trading_support/_option_positions_momentum.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/_option_positions_momentum.py>)
- [Python Scripts/services/position_manager.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/position_manager.py>)
- [Python Scripts/services/config.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/config.py>)
- [.env.example](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/.env.example>)

## Big Picture

The option manager does not just check one profit target and sell everything.

Instead, each management cycle does this:

1. Load the current broker position and local per-position state.
2. Reconcile any pending exit order from earlier cycles.
3. Build a fresh position snapshot with:
   - option quote
   - underlying stock quote
   - current P/L
   - DTE bucket
   - momentum data
4. Run the structured exit engine.
5. Return one action:
   - `hold`
   - `sell_partial`
   - `sell_full`
6. Optionally submit that order if live execution is enabled.

## Exit Priority

The structured exit engine evaluates exits in this order:

1. Invalid critical data
2. No position quantity
3. Recently reconciled fill hold
4. Existing pending exit order hold
5. Stop-loss
6. Near-expiration exit
7. Static take-profit logic if trailing profit is disabled
8. Trailing-profit protection and floor updates
9. Trailing-profit stop if price falls through the protected floor
10. Momentum-history full exit
11. First scale-out
12. Second scale-out
13. Hold

This ordering matters.

For example:

- a stop-loss beats momentum
- a trailing-profit stop beats scale-outs
- a pending exit order blocks new exit submissions

## DTE Buckets

The exit engine uses shared DTE buckets from:

- [Python Scripts/services/option_dte_buckets.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/option_dte_buckets.py>)

Those buckets drive:

- DTE-specific take-profit / stop-loss thresholds
- force-expiry overrides
- trailing giveback configuration

So a `7_14` contract and a `45_60` contract can use different exit tuning.

## Core Trailing-Profit Settings

These are the main trailing-profit settings in [`.env.example`](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/.env.example>):

```env
OPTION_POSITION_ENABLE_TRAILING_PROFIT=false
OPTION_POSITION_TRAILING_PROFIT_DRY_RUN=false
OPTION_TRAIL_PROTECTION_TRIGGER_PCT=0.40
OPTION_TRAIL_INITIAL_FLOOR_PCT=0.10
OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT=0.55
OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION=0.50
OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT=2.00
OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION=0.25
OPTION_TRAIL_7_14_GIVEBACK_PCT=0.35
OPTION_TRAIL_14_30_GIVEBACK_PCT=0.45
OPTION_TRAIL_30_45_GIVEBACK_PCT=0.45
OPTION_TRAIL_45_60_GIVEBACK_PCT=0.45
OPTION_TRAIL_100_FLOOR_PCT=0.60
OPTION_TRAIL_150_FLOOR_PCT=1.00
OPTION_TRAIL_200_FLOOR_PCT=1.40
```

### What the numbers mean

These values are ratios, not whole percentages:

- `0.40` means `+40%`
- `1.00` means `+100%`
- `2.00` means `+200%`

## How Profit Protection Starts

Trailing protection does not start immediately.

It activates only after:

- `current max pnl >= OPTION_TRAIL_PROTECTION_TRIGGER_PCT`

With default settings:

- trigger = `0.40`
- initial floor = `0.10`

Example:

1. You buy an option at `$2.00`
2. It rises to `$2.80`
3. That is `+40%`
4. Protection activates
5. The engine starts defending at least `+10%`

That `+10%` is the first protected floor.

## What the Protected Floor Means

The protected floor is the minimum profit level the engine is currently trying to defend.

If unrealized P/L falls below that floor, the remaining position can be closed with:

- `exit_reason = "trailing_profit_stop"`

The floor can come from:

- initial protection floor
- bucket-specific giveback logic
- milestone floors at `+100%`, `+150%`, `+200%`

The effective floor is the strictest one currently in force.

## Bucket-Specific Giveback

Giveback settings define how much of the peak profit the trade is allowed to lose before the trailing exit fires.

Example settings:

```env
OPTION_TRAIL_7_14_GIVEBACK_PCT=0.35
OPTION_TRAIL_14_30_GIVEBACK_PCT=0.45
OPTION_TRAIL_30_45_GIVEBACK_PCT=0.45
OPTION_TRAIL_45_60_GIVEBACK_PCT=0.45
```

Example for `7_14`:

1. Peak profit reaches `+100%`
2. Allowed giveback = `35%` of that gain
3. Protected floor becomes about `+65%`

Example for `14_30`:

1. Peak profit reaches `+100%`
2. Allowed giveback = `45%`
3. Protected floor becomes about `+55%`

So:

- lower giveback = tighter trailing stop
- higher giveback = more room to breathe

## Milestone Profit Floors

These settings tighten protection on large winners:

```env
OPTION_TRAIL_100_FLOOR_PCT=0.60
OPTION_TRAIL_150_FLOOR_PCT=1.00
OPTION_TRAIL_200_FLOOR_PCT=1.40
```

Meaning:

- once max profit reaches `+100%`, protect at least `+60%`
- once max profit reaches `+150%`, protect at least `+100%`
- once max profit reaches `+200%`, protect at least `+140%`

These can override looser bucket giveback behavior.

## Partial Scale-Outs

The engine supports two milestone scale-outs.

Settings:

```env
OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT=0.55
OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION=0.50
OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT=2.00
OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION=0.25
```

### First scale-out

Example:

1. You start with `4` contracts
2. Profit reaches `+55%`
3. First scale-out triggers
4. `50%` of the current quantity is sold
5. `2` contracts are sold

### Second scale-out

Example:

1. You still hold `2` contracts after the first scale-out
2. Profit reaches `+200%`
3. Second scale-out triggers
4. `25%` of the current quantity is sold
5. rounding rules apply

Note:

- scale-out quantity is computed from the current quantity
- it is not strictly a percent of original starting size

## Example: Full Trailing-Profit Lifecycle

Assume:

- entry price = `$2.00`
- quantity = `4`
- bucket = `7_14`
- first scale-out trigger = `+55%`
- first scale-out fraction = `50%`
- second scale-out trigger = `+200%`
- second scale-out fraction = `25%`
- giveback = `35%`

Sequence:

1. Option rises to `$2.80`
   - profit = `+40%`
   - protection activates
   - floor starts at `+10%`
2. Option rises to `$3.10`
   - profit = `+55%`
   - first scale-out triggers
   - sell `2` contracts
3. Option rises to `$4.00`
   - profit = `+100%`
   - `7_14` giveback would imply a floor around `+65%`
   - `OPTION_TRAIL_100_FLOOR_PCT=0.60`
   - effective floor is about `+65%`
4. Option rises to `$6.00`
   - profit = `+200%`
   - second scale-out can trigger
   - milestone floor rises to at least `+140%`
5. Option falls back hard
   - if unrealized profit drops below the protected floor
   - remaining contracts are sold with `trailing_profit_stop`

## Pending Exit Handling

If a close order is already pending, the engine will not keep submitting new exits on every cycle.

Instead it:

- remembers the pending order in local state
- checks its broker status every cycle
- clears it when terminal
- can cancel stale open exits after a timeout

Relevant settings:

```env
OPTION_PENDING_EXIT_STALE_MINUTES=10
OPTION_PENDING_EXIT_CANCEL_ON_STALE=true
```

This helps prevent repeated duplicate exit orders.

## Momentum-Based Exit

Momentum exit is now based on two layers:

1. option-price momentum
2. underlying-stock momentum

The combined status can be:

- `good`
- `bad`
- `mixed`
- `unknown`

### Why both are used

Option-price momentum is the primary signal because:

- it is based on the asset you actually own
- Alpaca exposes option entry price directly
- it remains usable after restarts

Underlying-stock momentum is the secondary signal because:

- it helps preserve thesis-aware directional context
- but it should not be trusted alone when the entry stock baseline is weak

## Option-Price Momentum

Current option-price momentum uses:

- entry option price
- current option mid price
- current unrealized P/L
- tracked peak P/L

The thresholds are now configurable:

```env
OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD=0.35
OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD=0.15
OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD=0.10
```

### Option-price status rules

Bad:

- option premium gave back at least the configured bad threshold from the tracked peak
- or current option return is below entry

Mixed:

- option premium gave back a noticeable amount, but not enough to be classified bad
- or it is hovering around entry without a strong continuation signal

Good:

- option premium is still above the configured good-profit threshold
- and is not showing meaningful giveback

Unknown:

- current option pricing is not usable

### Example: option-price momentum

Assume:

- entry option price = `$2.00`
- current option price = `$2.36`
- current profit ratio = `+18%`
- max profit ratio = `+50%`
- bad giveback threshold = `0.35`
- mixed giveback threshold = `0.15`
- good profit threshold = `0.10`

Then:

- giveback from peak = `0.50 - 0.18 = 0.32`
- `0.32` is below `0.35` bad threshold
- `0.32` is above `0.15` mixed threshold

Result:

- option-price momentum = `mixed`

## Underlying-Stock Momentum

Underlying momentum still uses:

- contract type
- entry underlying price
- current underlying stock price
- strike

Its output remains:

- `good`
- `bad`
- `unknown`

It is meant to answer:

- is the stock still moving in the profitable direction?
- is it still moving toward or through the strike?

## Combined Momentum

The engine combines option and underlying signals.

### Combination rules

If option momentum is `unknown`:

- combined = `unknown`

If option momentum is `bad`:

- combined = `mixed` when underlying is `good`
- otherwise combined = `bad`

If option momentum is `good`:

- combined = `mixed` when underlying is `bad`
- otherwise combined = `good`

If option momentum is `mixed`:

- combined = `bad` when underlying is `bad`
- otherwise combined = `mixed`

This keeps option-price behavior primary while still letting the stock context influence the result.

## Momentum History Tracking

Momentum-history exit is not based on a single bad sample.

It only activates after:

```env
OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT=2.00
```

Meaning:

- the system starts tracking momentum history only after the position’s max unrealized profit reaches `+200%`

Other settings:

```env
OPTION_MOMENTUM_HISTORY_WINDOW_SIZE=10
OPTION_MOMENTUM_HISTORY_MIN_SAMPLES=5
OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD=6
OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD=3
```

## Weighted Negative Scoring

The momentum-history exit now uses weighted negative scoring:

- `bad = 1.0`
- `mixed = 0.5`
- `good = 0.0`
- `unknown = 0.0`

This score is stored as:

- `momentum_negative_score`

It is accumulated from recent samples in the rolling window.

### Example: weighted score

Suppose the recent combined momentum history is:

- `bad`
- `mixed`
- `bad`
- `mixed`
- `bad`
- `good`

Score:

- `1.0 + 0.5 + 1.0 + 0.5 + 1.0 + 0.0 = 4.0`

So if your weighted exit threshold were `4`, that history would trigger a momentum exit.

## Momentum History Exit Conditions

The engine can full-exit for momentum history when:

1. tracking is active
2. minimum sample count has been reached
3. momentum exits are enabled
4. either:
   - weighted negative score reaches the configured threshold
   - consecutive bad samples reaches the configured threshold

The exit reason is:

- `momentum_history_failed`

## Example: momentum history full exit

Assume:

- tracking activated after `+200%`
- window size = `10`
- minimum samples = `5`
- weighted threshold = `6`
- consecutive-bad threshold = `3`

Recent combined momentum samples:

- `bad`
- `mixed`
- `bad`
- `mixed`
- `bad`
- `bad`
- `mixed`

Weighted score:

- `1 + 0.5 + 1 + 0.5 + 1 + 1 + 0.5 = 5.5`

That is still below `6`, so no momentum-history exit yet.

If the next sample is:

- `bad`

New score:

- `6.5`

Now the engine can full-exit with:

- `exit_reason = "momentum_history_failed"`

## Example: Mixed But Not Yet Bad Enough

Suppose:

- option price has rolled over some
- stock still looks decent
- combined momentum becomes `mixed`

One `mixed` sample adds only `0.5` to the negative score.

That means:

- a little deterioration is logged
- but the engine does not immediately bail out

This is the main point of the compromise model:

- let the option premium drive the decision
- but do not overreact to early soft weakness

## Dry Run Mode

If trailing profit dry run is enabled:

```env
OPTION_POSITION_TRAILING_PROFIT_DRY_RUN=true
```

The engine still computes:

- `sell_partial`
- `sell_full`
- trailing stops
- momentum-history exits

But it does not submit live sell orders.

This is useful for tuning and observation before turning live execution on.

## Fields You Will See In Output

The option manager output includes fields such as:

- `exit_action`
- `exit_reason`
- `qty_to_sell`
- `sell_fraction`
- `max_pnl_pct`
- `profit_protection_active`
- `protected_profit_floor_pct`
- `trailing_giveback_pct`
- `momentum_status`
- `momentum_reasons`
- `option_price_momentum_status`
- `option_price_momentum_reasons`
- `underlying_momentum_status`
- `underlying_momentum_reasons`
- `momentum_history_sample_count`
- `momentum_bad_count`
- `momentum_mixed_count`
- `momentum_negative_score`
- `momentum_consecutive_bad_count`

Those fields are the easiest way to debug why a position was held, scaled out, or fully exited.

## Practical Tuning Notes

If the engine feels too eager:

- raise `OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD`
- raise `OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD`
- raise `OPTION_MOMENTUM_HISTORY_MIN_SAMPLES`
- raise bucket giveback percentages

If the engine feels too slow:

- lower `OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD`
- lower `OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD`
- lower `OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD`
- lower `OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT`

If you want stronger protection for shorter-dated contracts:

- reduce `OPTION_TRAIL_7_14_GIVEBACK_PCT`
- reduce `OPTION_TRAIL_14_30_GIVEBACK_PCT`

## Summary

The current exit engine behaves like this:

- static stop-loss and near-expiration exits remain hard safety rails
- trailing profit protects winners instead of instantly selling them
- milestone scale-outs reduce size along the way
- option-price momentum is now configurable and primary
- underlying-stock momentum remains secondary context
- momentum-history exits use weighted negative scoring:
  - `bad = 1.0`
  - `mixed = 0.5`

This gives the bot a more gradual and stateful way to manage large winners than a single all-or-nothing take-profit rule.
