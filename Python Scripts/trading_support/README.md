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

- `services/position_manager.py`
  Calls `ManageCurrentOptionPositions(...)` from `option_positions.py`
- `services/trading_gateway.py`
  Provides a newer broker wrapper, but `trading_support/` still contains direct Alpaca helpers used by older code paths
- `Trading.py`
  Historically used these helpers directly and still forwards into the option-position workflow

So if `services/` is the runtime control layer, `trading_support/` is the lower-level trading helper layer that actually knows how to inspect and manage broker-facing trading data.

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

This is the main option-position exit engine for the repo.

It is the most important file in `trading_support/`, and it is what the newer `services.position_manager.OptionPositionManagerService` ultimately calls.

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

Key internal helper groups:

- Quote and price helpers:
  `_get_latest_option_quote(...)`, `_get_latest_stock_price(...)`
- Expiration and symbol parsing:
  `_parse_option_symbol(...)`, `_days_to_expiration(...)`, `_hours_to_expiration(...)`
- Threshold resolution:
  `_resolve_dte_exit_rule(...)`, `_resolve_option_exit_thresholds(...)`
- Trailing-profit behavior:
  `_resolve_trailing_giveback_pct(...)`, `_structured_exit_action(...)`
- Pending-order reconciliation:
  `_reconcile_pending_order_state(...)`, `_resolve_pending_fill_metrics(...)`, `_resolve_pending_order_age_seconds(...)`
- Snapshot building:
  `_build_option_position_snapshot(...)`

Important current design details:

- DTE bucket behavior is driven by `services.option_dte_buckets`
- DTE exit thresholds come from `services.config.OptionPositionSettings`
- Trailing-profit giveback percentages are now dynamic by bucket key instead of hardcoded by label
- Position state persistence is handled by `services.option_position_state`
- Momentum exits reuse `services.option_momentum`

If you need to understand why an option position was held, partially exited, or closed, this is the file to read first.

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
- `option_positions.py` is large because it combines inspection, decisioning, trailing state, and order reconciliation into one workflow. The newer service layer wraps it rather than replacing it outright.
