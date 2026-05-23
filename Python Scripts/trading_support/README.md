# Trading Support Folder

This folder contains the lower-level trading helpers that talk to Alpaca, inspect account/position data, and implement option-position exit logic.

Compared with `services/`, which is the newer orchestration layer, `trading_support/` is closer to the raw trading operations themselves.

In practice, this folder is where the repo keeps:

- Alpaca client helpers
- account and contract lookup helpers
- stock order submission helpers
- the main option-position exit engine
- small numeric/quote utilities

## How This Folder Fits Into The Runtime

The newer service layer usually calls into `trading_support/` instead of reimplementing broker logic.

The most important connection points are:

- `services/front_main_application.py`
  Schedules when option-position management should run.
- `services/position_manager.py`
  Calls `ManageCurrentOptionPositions(...)` from `option_positions.py` and supplies the broker callbacks.
- `services/trading_gateway.py`
  Provides the newer normalized Alpaca wrapper used by the service layer.
- `Trading.py`
  Preserves the older compatibility import path and still forwards into the option-position workflow.

So if `services/` is the runtime control layer, `trading_support/` is the lower-level trading helper layer that knows how to inspect positions, compute exits, reconcile orders, and translate that into broker-facing actions.

## Module Guide

### `__init__.py`

Package export surface for the `trading_support` package.

It re-exports the legacy high-level helpers most other modules expect:

- `GetAccountDiagnostics`
- `InitializeTradingClient`
- `IntializeTradingClient`
- `get_default_trading_client`
- `get_option_history_client`
- `get_stock_history_client`
- `GetCallOptionsForCompany`
- `CloseOptionPositions`
- `ManageCurrentOptionPositions`
- `OPTION_EXIT_DTE_RULES`
- `StockTrades`

Use this module if you want the older “single import surface” style.

### `utils.py`

Tiny shared numeric helpers used by the trading helpers.

Importable functions:

- `safe_float(value)`
  Converts a value to `float` when possible and returns `None` when it cannot.
- `mid_price(bid_price, ask_price)`
  Returns the midpoint between bid and ask when both are available.

This is the smallest and safest place to reuse quote/math helpers from this package.

### `clients.py`

Alpaca client setup helpers and cached client accessors.

Importable functions:

- `InitializeTradingClient(api_key, secret, paper)`
  Creates an Alpaca `TradingClient` with key/secret auth.
- `IntializeTradingClient(api_key, secret, paper)`
  Backward-compatible alias for the original misspelled helper name.
- `get_default_trading_client()`
  Returns the cached default Alpaca trading client from env settings.
- `get_option_history_client()`
  Returns the cached Alpaca option-history client.
- `get_stock_history_client()`
  Returns the cached Alpaca stock-history client.

Importable constants and proxies:

- `ALPACA_PAPER`
  Whether the default client is configured for paper trading.
- `trading_client`
  Lazy proxy to `get_default_trading_client()`
- `option_history_client`
  Lazy proxy to `get_option_history_client()`
- `stock_history_client`
  Lazy proxy to `get_stock_history_client()`

Important internal pieces:

- `_get_alpaca_settings()`
  Loads typed Alpaca settings from `services.config`.
- `_LazyClientProxy`
  Defers actual client creation until an attribute is used.

This file is the best place to import broker/data clients when you want the older trading helper path instead of the newer `services.trading_gateway` wrapper.

### `account.py`

Simple account-inspection helper for the fields this project uses most often.

Importable function:

- `GetAccountDiagnostics(trading_client_override=None, echo=True)`
  Returns a dictionary of key Alpaca account fields such as buying power, cash, equity, options approval level, and trading-block flags.

Use this for diagnostics, debugging, or dashboard-friendly account snapshots.

### `option_contracts.py`

Small option-chain sampling helper.

Importable function:

- `GetCallOptionsForCompany(company, trading_client_override=None, expiration_date=..., limit=...)`
  Fetches a small sample of call contracts for one underlying from Alpaca.

This is a lightweight helper for quick contract inspection, not the full contract-selection engine.

### `stock_orders.py`

Legacy helper for direct stock buy/sell submissions.

Importable class:

- `StockTrades`
  Small stateful helper for immediate or limit stock buy/sell actions.

Key methods:

- `ImmediateStockBuy(numOfStocks=0)`
- `StockAtPriceBuy(numOfStocks=0, limit_price=0, notional=0)`
- `ImmediateStockSell(numOfStocks=0)`
- `StockAtPriceSell(numOfStocks=0, limit_price=0, notional=0)`

Important internal helper:

- `_resolve_time_in_force(value)`
  Maps text like `DAY`, `GTC`, `IOC`, and `FOK` to Alpaca enums.

This file is mostly a simple convenience wrapper around stock order submission and is less central than the option helpers.

### `option_positions.py`

This is now the public facade for the option-position engine.

It is still the main import surface that the newer `services.position_manager.OptionPositionManagerService` ultimately calls, but the implementation has been split across several focused internal modules.

Main responsibilities:

- inspect open option positions
- compute profit/loss and time-to-expiration metrics
- apply DTE-bucket-specific take-profit and stop-loss rules
- apply dynamic trailing-profit logic
- optionally apply momentum-based exits
- track pending exit orders across cycles
- submit full or partial exits
- return a normalized summary payload for every open option position

Importable dataclasses:

- `OptionExitRule`
  One resolved DTE-bucket-specific option exit rule.
- `ExitThresholds`
  The final threshold values chosen for one position.
- `TrailingProfitConfig`
  Trailing-profit configuration bundle, including bucket-specific giveback mapping.
- `PendingExitConfig`
  Configuration for stale pending-exit handling.

Important exported constants:

- `DEFAULT_OPTION_TAKE_PROFIT_PCT`
- `DEFAULT_OPTION_STOP_LOSS_PCT`
- `DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION`
- `OPTION_EXIT_DTE_RULES`

Main public functions:

- `CloseOptionPositions(company=None, trading_client_override=None)`
  Immediately closes every matching option position and returns a compact summary of what was submitted.
- `ManageCurrentOptionPositions(...)`
  Runs the full option-position management workflow and returns a normalized result payload describing every open option position, its diagnostics, and any submitted exit actions.

Current internal module split:

- `_option_positions_defaults.py`
  Shared dataclasses, default constants, and bundled config types for the option-position workflow.
- `_option_positions_market.py`
  Symbol parsing, DTE calculations, quote helpers, and exit-threshold resolution.
- `_option_positions_snapshot.py`
  Builds one normalized position snapshot, including quote context, momentum summaries, and persisted-state updates.
- `_option_positions_strategy.py`
  Applies stop-loss, expiry, trailing-profit, scale-out, and momentum-history exit rules.
- `_option_positions_state.py`
  Handles pending-order reconciliation and momentum/trailing state bookkeeping between cycles.
- `_option_positions_momentum.py`
  Combines option-price momentum with underlying-stock momentum into the weighted momentum signal used by the strategy layer.
- `_option_positions_manager.py`
  Orchestrates the full multi-position management pass and handles sell submission callbacks.

Important current design details:

- DTE bucket behavior is driven by `services.option_dte_buckets`
- DTE exit thresholds come from `services.config.OptionPositionSettings`
- Trailing-profit giveback percentages are now dynamic by bucket key instead of hardcoded by label
- Position state persistence is handled by `services.option_position_state`
- Momentum exits reuse `services.option_momentum`

If you need to understand why an option position was held, partially exited, or closed, this is the file to read first.

If you need to change the actual logic, the best entrypoint is usually:

- `option_positions.py` to understand the public surface
- then the relevant `_option_positions_*` module that owns the behavior you want to change

## Which Imports Are Most Useful?

If you are using the older trading helper surface directly, the most useful imports are usually:

```python
from trading_support.clients import get_default_trading_client
from trading_support.account import GetAccountDiagnostics
from trading_support.option_contracts import GetCallOptionsForCompany
from trading_support.option_positions import (
    CloseOptionPositions,
    ManageCurrentOptionPositions,
    OPTION_EXIT_DTE_RULES,
)
from trading_support.stock_orders import StockTrades
```

If you want the package-level re-export surface instead:

```python
from trading_support import (
    GetAccountDiagnostics,
    GetCallOptionsForCompany,
    ManageCurrentOptionPositions,
    CloseOptionPositions,
    StockTrades,
)
```

## Newer vs Older Paths

There are two styles in the repo right now:

- Newer service-layer orchestration
  Uses `services.front_main_application`, `services.position_manager`, `services.trade_executor`, and `services.trading_gateway`
- Older direct helper usage
  Uses `trading_support.*` functions and `Trading.py`

Both still matter, but for new runtime orchestration work, the service layer is usually the better place to start.

## Notes

- Functions and helpers starting with `_` are internal even if they are technically importable.
- Some names here intentionally preserve old spellings and old call shapes for backward compatibility.
- `option_positions.py` is no longer a single large implementation file; it is the compatibility facade over the split `_option_positions_*` modules.
- The newer service layer wraps this package rather than replacing it outright, so changes here still directly affect live option-position management.
