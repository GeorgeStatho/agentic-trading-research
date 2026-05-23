# Option Trailing-Profit Progress

This document tracks the current state of the dynamic take-profit / trailing exit work for option position management.

## Goal

Replace the old static "sell the whole position at take-profit" behavior with a stateful exit system that can:

- protect gains after the trade starts working
- scale out partially at milestone profits
- trail the remaining position instead of closing it immediately
- keep stop-loss and expiration exits as higher-priority safety rails

## Changes Already Made

### 1. Configuration surface added

Implemented in:

- [Python Scripts/services/config.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/config.py>)
- [.env.example](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/.env.example>)

Added:

- `OPTION_POSITION_ENABLE_TRAILING_PROFIT`
- `OPTION_POSITION_TRAILING_PROFIT_DRY_RUN`
- `OPTION_POSITION_STATE_PATH`
- trailing-profit trigger, floor, scale-out, and giveback settings

Status: done

### 2. Position-state persistence created

Implemented in:

- [Python Scripts/services/option_position_state.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/option_position_state.py>)

Added:

- `load_state`
- `save_state`
- `get_position_state`
- `update_position_state`
- `delete_position_state`

Behavior:

- handles missing file
- handles corrupt JSON safely
- writes atomically
- stores a top-level `schema_version`

Status: done

### 3. Structured exit engine added to option manager

Implemented in:

- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)

Added:

- structured actions: `hold`, `sell_partial`, `sell_full`
- position state loading and persistence per cycle
- tracked `max_pnl_pct`
- profit-protection activation
- DTE-based trailing giveback logic
- milestone floors for `+100%`, `+150%`, `+200%`
- dry-run-aware execution path

Current behavior:

- stop-loss and near-expiration exits still have higher priority
- static behavior still works when trailing profit is disabled
- 1-contract partial exits are normalized to full exits

Status: done

### 4. Richer option position output added

Implemented in:

- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)
- [Python Scripts/services/position_manager.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/position_manager.py>)

Added output fields:

- `exit_action`
- `exit_reason`
- `qty_to_sell`
- `sell_fraction`
- `max_pnl_pct`
- `profit_protection_active`
- `protected_profit_floor_pct`
- `trailing_giveback_pct`
- `took_first_scale_out`
- `took_second_scale_out`
- `pending_order_id`
- `pending_order_status`
- `pending_order_last_checked_at`
- `pending_order_terminal`
- `pending_order_error`
- `pending_order_age_seconds`
- `pending_order_stale`
- `pending_order_cancel_submitted`
- `pending_order_filled_qty`
- `pending_order_remaining_qty`
- `last_action`
- `last_action_at`
- `momentum_status`
- `momentum_reasons`
- `close_order_id`
- `close_order_status`
- `close_order_side`
- `close_requested_qty`
- `full_sell_count`
- `partial_sell_count`

Status: done

### 5. Partial option sell support added to trading gateway

Implemented in:

- [Python Scripts/services/trading_gateway.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/trading_gateway.py>)

Added:

- `submit_option_sell_market_order(...)`
- `close_option_position(...)`

Behavior:

- validates symbol
- validates quantity
- checks current open broker quantity before selling
- returns normalized order payloads for both partial and full exits

Status: done

### 6. Position manager execution wiring updated

Implemented in:

- [Python Scripts/services/position_manager.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/position_manager.py>)
- [Python Scripts/main.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/main.py>)

Changed:

- manager now passes the configured state path
- manager now passes trailing-profit config flags directly
- manager now injects full-exit and partial-exit callbacks from `trading_gateway`
- manager now injects order-status resolution for pending-order reconciliation

Status: done

### 7. Trade journal extended for managed exits

Implemented in:

- [Python Scripts/services/trade_journal.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/trade_journal.py>)
- [Python Scripts/services/position_manager.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/position_manager.py>)

Added:

- `record_position_exit(...)`

Behavior:

- logs submitted managed exits into the existing `option_trade_executions` table
- marks exit rows with:
  - `selected_option_source = "position_manager"`
  - `confidence = "managed_exit"`

Status: done

### 8. Option momentum helper created and wired into the exit engine

Implemented in:

- [Python Scripts/services/option_momentum.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/option_momentum.py>)
- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)

Added:

- `evaluate_option_momentum(...)`
- `evaluate_option_momentum_from_snapshot(...)`

Behavior:

- returns `good`, `bad`, or `unknown`
- uses simple first-pass logic:
  - whether the underlying moved in the profitable direction
  - whether the underlying moved closer to the strike
- momentum is now included in the position-manager output
- the structured exit engine can force a full exit with `momentum_failed_after_tp` after profit protection has already become active and momentum turns bad

Status: done

### 9. Pending-order reconciliation added

Implemented in:

- [Python Scripts/services/trading_gateway.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/trading_gateway.py>)
- [Python Scripts/services/position_manager.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/position_manager.py>)
- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)

Added:

- `get_order_status_summary(...)`
- `cancel_order(...)`
- per-cycle pending-order reconciliation before exit decisions are made

Behavior:

- open pending orders continue to block new exit submissions
- filled, canceled, rejected, and expired orders are cleared out of local state
- stale open exit orders can now be auto-canceled after a timeout so the next cycle can retry
- partial-fill quantity and remaining-order metadata are now surfaced in the position output and persisted in local state
- retryable terminal exits keep partial-fill progress instead of blindly rolling back scale-out state
- reconciliation metadata is surfaced in the position output
- a one-cycle `recent_fill_reconciliation` hold is used after a filled exit so the bot does not race the broker position snapshot

Status: done

## Changes Partially Made

### 1. Pending-order reconciliation exists, but lifecycle handling is not complete

Current state:

- pending orders are written into state after submission
- the manager re-checks pending order status every cycle
- terminal statuses are cleared from local state
- stale pending orders can be auto-canceled and retried on the next cycle
- partial fills now preserve fill progress instead of always resetting scale-out flags
- recently filled exits get a one-cycle hold to avoid immediate resubmission

Missing:

- more explicit retry policy after cancel / reject
- deeper broker-side quantity reconciliation when an order partially fills and remains open

Status: partial

### 2. State exists, but the full schema is not enforced yet

Current state:

- the state helper stores arbitrary update payloads safely
- the option manager already writes key fields such as:
  - `max_pnl_pct`
  - `profit_protection_active`
  - `protected_profit_floor_pct`
  - `took_first_scale_out`
  - `took_second_scale_out`
  - pending-order metadata

Missing:

- full canonical schema enforcement
- explicit state migration rules when schema changes

Status: partial

## Changes Still To Be Made

### 1. Deepen pending-order lifecycle handling

Needed in:

- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)
- [Python Scripts/services/trading_gateway.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/trading_gateway.py>)

Work:

- add clearer retry policy after cancel / reject
- deepen broker reconciliation while an order is still partially filled and open

Priority: high

### 2. Reconcile local state with broker state more fully

Needed in:

- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)

Work:

- update `remaining_qty` from broker quantity every cycle
- reset or clean state more deliberately after position is fully gone
- record `last_filled_at` when available

Priority: high

### 3. Expand state fields toward the full planned schema

Needed in:

- [Python Scripts/services/option_position_state.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/option_position_state.py>)
- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)

Still planned:

- `realized_pnl_dollars`
- `realized_cost_basis_dollars` or equivalent realized basis fields
- `entry_delta`
- `entry_iv`
- `last_filled_at`

Priority: medium

### 4. Improve trade journal detail for managed exits

Needed in:

- [Python Scripts/services/trade_journal.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/trade_journal.py>)

Possible improvements:

- include more explicit action labels for partial vs full exits
- persist realized exit economics when available
- add a dedicated exit-event file or JSONL audit log if DB history alone feels too coarse

Priority: medium

### 5. Improve momentum sophistication

Needed in:

- [Python Scripts/services/option_momentum.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/services/option_momentum.py>)
- [Python Scripts/trading_support/option_positions.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/trading_support/option_positions.py>)

Still planned:

- incorporate more than entry-price vs strike-direction logic
- optionally add VWAP, ATR, recent return, or trend context
- refine when `unknown` should stay neutral versus trigger defensive exits

Priority: medium

### 6. Add replay / backtest comparison modes

Likely target:

- [Python Scripts/options_backtest.py](</mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python Scripts/options_backtest.py>)

Still planned:

- compare static take-profit vs trailing-profit behavior
- measure missed upside after early exits
- count scale-outs, trailing exits, and round-trips

Priority: medium

### 7. Add focused tests

Missing coverage:

- state file lifecycle
- first scale-out logic
- second scale-out logic
- single-contract normalization
- dry-run execution behavior
- managed-exit journal mapping

Recently added:

- pending stale-order cancel path
- pending-order terminal reconciliation with and without partial fills
- momentum-driven exit behavior

Priority: high

## Recommended Next Steps

1. Add focused tests for momentum and pending-order reconciliation behavior.
2. Add timeout and retry handling for stale pending orders.
3. Improve broker-state reconciliation after fills and partial fills.
4. Run the system in dry-run or paper mode and inspect real position-manager output.
5. Only after that, consider tightening exit behavior further or expanding backtesting support.

## Current Overall Status

The trailing-profit architecture is now mostly in place:

- config exists
- state exists
- structured exit actions exist
- partial sell submission exists
- managed exits can be journaled
- momentum can influence post-profit exits
- pending exit orders can be reconciled and cleared

The biggest remaining gap is still post-submission lifecycle depth, not architecture. The manager now knows how to reconcile and clear pending exits, but it still needs stronger handling for stale orders, partial fills, and fuller broker-state synchronization.
