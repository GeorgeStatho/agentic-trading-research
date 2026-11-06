# Services Folder

This folder holds the backend service layer that sits between the agent pipeline, the trading helpers, the Flask API, and the worker entrypoints.

In practice, `services/` is where the repo keeps:

- typed runtime config and shared paths
- trading orchestration
- order execution and broker access
- option-position maintenance helpers
- persistence helpers for status, state, and trade journaling

If `agentCallers/` is the analysis and recommendation side of the system, `services/` is the runtime and execution side.

## Layout

Modules are grouped into subpackages by domain. Import them with the full path, for example
`from services.core.settings import load_process_settings`.

| Subpackage | What belongs there |
|---|---|
| `core/` | Shared configuration, paths, env parsing and small utilities. |
| `runtime/` | Worker orchestration, startup checks and runtime telemetry. |
| `trading/` | Broker access, order building, execution, journaling and follower accounts. |
| `options/` | Option contracts: DTE buckets, pricing, symbols, position state and exits. |
| `gates/` | Deterministic entry gates and their rollout policies. |
| `thesis/` | Strategist theses: capture, claim checks, validation and event reviews. |
| `research/` | Shadow research loop: contracts, inputs, orchestration, promotion and marks. |
| `discovery/` | Event discovery, catalyst classification and company evidence collection. |
| `evaluation/` | Backtests, benchmarks, forward evaluation and decision attribution. |

Recent additions worth knowing about:

- `trading/trading_accounts.py`, `trading/account_strategies.py`, `trading/follower_fleet.py`: follower paper accounts. They mirror production's entries into other strategies, and production stays read-only.
- `discovery/market_events.py`: EDGAR's market-wide 8-K/6-K feed adds discovery interest for companies outside the ranking.
- `discovery/discovery_routing.py`: the admitted discovery entries that `DISCOVERY_MODE=enforce` sends to the production strategist and manager.
- `runtime/db_backup.py`: the nightly full database copy, plus the Litestream replica-lag check (see `docs/BACKUPS.md`).

When adding a module, put it in the subpackage whose description fits. Only create a new
subpackage when none does.

## How The Pieces Fit Together

The current trading runtime is layered rather than flat:

1. `runtime/worker_application.py`
   Owns the main worker cadence and decides when to run entry execution and when to run option-position maintenance.
2. `core/trading_config.py` and `core/runtime_paths.py`
   Provide the typed settings and file paths used by every runtime service.
3. `trading/trade_executor.py`
   Handles new entries after agent output has already been converted into executable order candidates.
4. `options/position_manager.py`
   Handles existing positions by calling the lower-level option-position workflow and wiring in broker callbacks.
5. `trading/trading_gateway.py`
   Normalizes Alpaca account, position, order, and cancel operations for the service layer.
6. `trading_support/option_positions.py`
   Acts as the compatibility facade for option-position management.
7. `trading_support/_option_positions_*.py`
   Hold the split implementation details for market inspection, snapshot building, trailing-profit strategy, pending-order state, and manager orchestration.
8. `trading/trade_journal.py`
   Records executed entries and managed exits to the database.

So the practical call flow for open positions is:

`WorkerApplication` -> `OptionPositionManagerService` -> `trading_support.option_positions.ManageCurrentOptionPositions(...)` -> split `_option_positions_*` helpers

Supporting modules like `core/env_parsing.py`, `core/numbers.py`, `core/status_files.py`, `options/option_dte_buckets.py`, `options/option_momentum.py`, and `options/option_position_state.py` provide shared utility logic used across both the service layer and the lower-level trading helpers.

## Module Guide

### `__init__.py`

Package marker for the `services` package.

There are no importable runtime helpers here right now.

### `core/runtime_paths.py`

Central location for path constants used across the service layer.

Exports:

- `SERVICES_DIR`
  Path to the current `services/` folder.
- `PYTHON_SCRIPTS_DIR`
  Path to `Python Scripts/`.
- `ROOT_DIR`
  Path to the repo root.
- `DATA_DIR`
  Path to the repo `Data/` folder.
- `LOGS_DIR`
  Path to `Data/logs/`.
- `ENV_PATH`
  Path to the repo `.env`.
- `AGENT_CALLERS_DIR`
  Path to `Python Scripts/agentCallers/`.

Use this module when a service needs a canonical repo path instead of rebuilding relative paths inline.

### `core/env_parsing.py`

Typed environment-variable readers. Each falls back to its default when the value is missing or malformed.

- `env_flag(name, default)`
  Any value except `0`, `false`, `no` or `off` is true.
- `env_percentage(name, default)`
  A float clamped to `0..100`.
- `env_positive_int(name, default)` / `env_non_negative_int(name, default)`
  Integers clamped to at least 1 / 0.
- `env_optional_positive_int(name)`
  A positive integer, or `None` when missing or invalid.
- `env_float(name, default)`

Use these instead of parsing `os.getenv` by hand so every reader agrees on what a value means.

### `core/numbers.py`

- `safe_float(value)`
  Best-effort float conversion (`None` for blanks and unparseable values), used throughout trading and market-data code.
- `finite_float(value)`
  Like `safe_float`, but NaN and infinity are `None` too.
- `safe_int(value)`
  `int()` conversion, `None` for blanks and unparseable values (`"1.5"` is `None`).

### `core/time_utils.py`

- `utc_now()` / `utc_now_iso()`
  The current time as an aware UTC datetime, or as ISO text ending in `+00:00`.
- `parse_timestamp(value)`
  ISO text (a trailing `Z` allowed) to an aware datetime that keeps its offset; naive text is UTC,
  anything unparseable is `None`.
- `parse_utc_timestamp(value)`
  The same, converted to UTC.

Import these rather than writing a local copy; `tests/test_core_helpers.py` fails if a removed copy
comes back. `catalyst_models.timestamp` is the one other parser, for callers that must not strip
padding.

### `options/option_symbols.py`

- `looks_like_option_symbol(symbol, asset_class="")`
  Detects an option by asset class or OCC-style symbol.
- `option_underlying_symbol(symbol)`
  The underlying ticker of an OCC option symbol.

### `core/trading_config.py`

Typed runtime configuration for the worker, the API, and shared option-position logic.

Importable dataclasses and helpers:

- `WorkerPaths`
  File paths used by the main trading worker.
  Important method: `WorkerPaths.from_env()`
- `WorkerSettings`
  Runtime settings for trading cycles, execution sizing, exposure limits, and scheduling. Exit settings are nested under `option_positions`.
  Important method: `WorkerSettings.from_env()`
- `OptionExitRuleConfig`
  One DTE-bucket-specific option exit configuration record.
- `OptionPositionSettings`
  Every option exit setting: thresholds, DTE rules, and the nested `TrailingProfitConfig`, `MomentumHistoryConfig`, `OptionPriceMomentumConfig` and `PendingExitConfig`. Used by the option manager, the API, and the pipeline manifest.
  Important method: `OptionPositionSettings.from_env()`
- `ApiPaths`
  Paths used by the Flask API when it reads runtime output files.
- `ApiSettings`
  API-level settings such as host, port, and CORS-related runtime values.
- `AlpacaSettings`
  Typed Alpaca connection settings.
- `AgentPipelineSettings`
  Settings used by the agent pipeline runtime.

Important internal helpers:

- `_load_exit_hours_to_expiration()`
  Reads the forced-exit window in hours, converting the older `*_DAYS_*` variable.
- `_load_option_trail_giveback_pct_by_bucket_key()`
  Builds trailing-profit giveback overrides dynamically from the shared DTE bucket list.

This file is the main source of truth for env-driven runtime behavior.

### `core/status_files.py`

Small file-writing utilities used by the worker and monitoring surfaces.

Importable classes:

- `StatusReporter`
  Writes a small status JSON payload for the dashboard and other monitors.
  Key method: `write(state, message, **extra)`
- `JsonFileWriter`
  Small static helper for persisting JSON payloads to disk.
  Key method: `write(path, payload)`

Use this module when you want output persistence to stay consistent across workers and scripts.

### `core/logging_utils.py`

The one place logging is set up. Every entrypoint calls `configure_logging()` once under
`if __name__ == "__main__":`:

- `configure_logging("news_collector")` logs INFO and above to stdout and to a new
  timestamped `LOGS_DIR/news_collector_<time>.log`, and returns that path.
- `configure_logging()` logs to stdout only.

Library and service code logs through `logging.getLogger(__name__)` and never calls
`print`; `tests/test_logging_setup.py` enforces both.

### `runtime/cold_start.py`

Performs the runtime’s database sanity check and optional initial market bootstrap.

Importable class:

- `ColdStartSanityChecker`
  Verifies that the market and news DBs exist and, when necessary, hydrates sector, industry, and company market data.
  Key method: `run()`

This is usually invoked early in worker startup so the rest of the runtime can assume the DB is in a usable state.

### `trading/order_candidates.py`

Converts agent output into normalized, executable option-order candidates.

Importable class:

- `OrderCandidateBuilder`
  Filters company recommendations down to the tradeable option entries the execution layer expects.

Key public methods:

- `build(agent_result)`
  Reads the normal `run_full_agent_stack...` output shape and returns a list of executable candidates.
- `build_from_company_results(company_results)`
  Builds candidates directly from company-level agent results.
- `build_from_manager_result(manager_result)`
  Builds one candidate from a single manager-stage result.

Important internal behavior:

- skips non-option decisions
- filters out ineligible confidence/decision combinations
- deduplicates repeated option symbols

### `trading/trading_gateway.py`

Broker adapter around Alpaca so the rest of the service layer can work with normalized plain-Python payloads.

Importable class:

- `AlpacaTradingGateway`
  Encapsulates Alpaca-specific client creation, account reads, position reads, and order submission.

Key methods:

- `create_client()`
  Builds the Alpaca `TradingClient`.
- `market_is_open(trading_client)`
  Checks the broker clock.
- `get_available_buying_power(trading_client)`
  Reads account buying power.
- `get_open_option_exposure(trading_client)`
  Summarizes current open option exposure and position count.
- `submit_option_market_order(trading_client, option_symbol, qty)`
  Submits a BUY market order for an option.
- `submit_option_sell_market_order(trading_client, option_symbol, qty)`
  Submits a SELL market order for part or all of an option position.
- `close_option_position(trading_client, option_symbol)`
  Closes an option position through Alpaca’s close-position path.
- `get_order_status_summary(trading_client, order_id)`
  Normalizes order status into a plain dictionary.
- `cancel_order(trading_client, order_id)`
  Cancels an order and returns its latest normalized status.

This is the module to change if broker-specific behavior needs to move without disturbing the rest of the runtime.

### `trading/trade_executor.py`

Handles new option-entry execution once the agent stack has already selected contracts.

Importable dataclasses and class:

- `TradeExecutionSession`
  Mutable execution-session state shared across multiple candidate submissions in one cycle.
- `OptionExposureSnapshot`
  Small immutable snapshot of current option exposure versus the configured buying-power cap.
- `OptionTradeExecutor`
  Sizes orders, enforces exposure limits, submits trades, and returns normalized execution summaries.

Key methods on `OptionTradeExecutor`:

- `create_session(trading_client, order_qty=None)`
  Initializes an execution session from account buying power.
- `execute_candidate(trading_client, candidate, session)`
  Executes one candidate immediately and mutates the shared session.
- `finalize_session(session)`
  Converts the session back into a trade-result payload.
- `execute(trading_client, order_candidates, order_qty=None)`
  Runs the full batch execution path for multiple selected candidates.

Important internal responsibilities:

- estimate contract cost from quote/trade data
- calculate order quantity from buying-power settings
- skip duplicates within one cycle
- prevent orders from exceeding the deployable-buying-power cap
- optionally record successful executions through `OptionTradeJournal`

### `trading/trade_journal.py`

Persists trade activity into the market database.

Importable class:

- `OptionTradeJournal`
  Records both newly executed option entries and managed exits.

Key methods:

- `record_execution(execution)`
  Persists a successful entry execution.
- `record_position_exit(position_summary)`
  Converts a managed exit summary into a trade-execution-style record and persists it.

This module keeps DB journaling separate from execution and exit logic.

### `options/position_manager.py`

Service wrapper around the existing option-position maintenance workflow.

Importable class:

- `OptionPositionManagerService`
  Runs option-position management, wires in broker callbacks, records exit activity, and persists the management output JSON.

Key method:

- `run_cycle(trading_client)`
  Calls `trading_support.option_positions.ManageCurrentOptionPositions(...)` with the current service-layer settings and broker callbacks.

Important responsibilities:

- passes take-profit, stop-loss, expiry, trailing-profit, momentum-exit, and stale-order settings into the underlying trading helper
- provides callbacks for full exits, partial exits, order-status lookups, and order cancellation
- acts as the boundary between the service layer and the split `trading_support/_option_positions_*` implementation
- records managed exits through `OptionTradeJournal`
- writes the latest position-management output file for the dashboard/runtime

### `options/option_dte_buckets.py`

Shared source of truth for option DTE bucket definitions and bucket-related helpers.

Importable dataclass:

- `DteBucket`
  Defines a DTE bucket’s key, label, day range, default targeting behavior, exit defaults, trailing defaults, and optional mapped time horizon.

Important exported constants:

- `OPTION_DTE_BUCKETS`
  Ordered bucket definitions.
- `ORDERED_OPTION_DTE_BUCKET_KEYS`
  Canonical bucket keys without `none`.
- `ORDERED_OPTION_DTE_BUCKET_KEYS_WITH_NONE`
  Canonical bucket keys plus `none`.
- `VALID_OPTION_DTE_BUCKET_KEYS`
  Set version of the canonical keys.
- `VALID_OPTION_DTE_BUCKET_KEYS_WITH_NONE`
  Set version of the canonical keys plus `none`.
- `OPTION_DTE_BUCKET_BY_KEY`
  Direct lookup by bucket key.
- `DTE_BUCKET_TO_TARGET_OTM_PCT`
  Default target OTM percentages by bucket.
- `TIME_HORIZON_TO_DTE_BUCKET`
  Mapping from strategist/manager time horizons to canonical DTE buckets.
- `DEFAULT_MANAGER_TARGET_DTE_BUCKET`
  Default manager bucket when a directional trade is allowed but none is specified.

Importable helper functions:

- `normalize_target_dte_bucket(value)`
  Normalizes bucket aliases like `7-14` into canonical keys like `7_14`.
- `get_all_target_dte_bucket_aliases()`
  Returns all accepted aliases.
- `get_target_dte_bucket_choices_text()`
  Human-readable text used in prompts and docs.
- `get_time_horizon_mapping_text()`
  Human-readable time-horizon mapping summary.
- `get_dte_bucket(bucket_key)`
  Returns the `DteBucket` for a key.
- `get_dte_bucket_range(bucket_key)`
  Returns `(min_days, max_days)` for a key.
- `get_bucket_target_otm_pct(bucket_key)`
  Returns the bucket’s target OTM percentage.
- `resolve_dte_bucket_for_days(days_to_expiration, prefer_higher_boundary=False)`
  Finds which bucket a raw DTE value belongs to.
- `target_dte_bucket_to_expiration_filters(bucket_key)`
  Converts a bucket key into expiration-date selection filters.

This module exists so manager logic, deterministic selection logic, and exit logic all use the same bucket definitions.

### `options/option_momentum.py`

Momentum-assessment helpers for existing option positions.

Importable constants and types:

- `MOMENTUM_GOOD`
- `MOMENTUM_BAD`
- `MOMENTUM_UNKNOWN`
- `MomentumStatus`

Importable dataclass:

- `OptionMomentumAssessment`
  Structured summary of whether the underlying has moved in a direction that supports the option position.

Importable functions:

- `evaluate_option_momentum(contract_type, entry_underlying_price, current_underlying_price, strike_price)`
  Returns a structured momentum assessment.
- `evaluate_option_momentum_from_snapshot(snapshot)`
  Convenience wrapper that reads the expected fields from a position snapshot dict.

This module is used by option-position management when momentum-based exits are enabled.

### `options/option_position_state.py`

Persistent state store for option-position management.

Importable functions:

- `build_empty_state()`
  Returns the default empty state payload.
- `load_state(path)`
  Loads the persisted JSON state file and normalizes it.
- `save_state(path, state)`
  Writes the full state file atomically.
- `get_position_state(state, option_symbol)`
  Reads one symbol’s state block.
- `update_position_state(state, option_symbol, updates)`
  Merges updates for one option symbol into the in-memory state.
- `delete_position_state(state, option_symbol)`
  Removes one option symbol from the in-memory state.

Important internal helpers:

- `_normalize_option_symbol(...)`
  Standardizes symbol keys.
- `_normalize_loaded_state(...)`
  Cleans loaded file contents into the expected shape.
- `_atomic_write_json(...)`
  Prevents partial-state-file writes.

This module lets trailing-profit and pending-exit logic survive across management cycles.

### `runtime/worker_application.py`

Top-level orchestration for the main worker loop.

Importable class:

- `WorkerApplication`
  Coordinates startup checks, market timing, agent execution, candidate selection, order execution, output persistence, and scheduled looping.

Key responsibilities:

- run the cold-start sanity check
- decide whether a cycle should be skipped because of Friday entry blocking or option-exposure limits
- run the agent stack with `run_full_agent_stack_from_existing_data()`
- convert agent output into order candidates
- execute those candidates
- run option-position management on its own cadence
- persist status and output snapshots for the dashboard

Common methods you will care about:

- `run_trading_cycle(...)`
  Runs one end-to-end agent plus trading cycle.
- `run_streaming_trading_cycle(...)`
  Runs the main combined cycle used by the long-running worker path.
- `run_main_loop(...)`
  Runs the worker loop that repeatedly checks the market and schedules trading/management work.

This is the main service-layer entrypoint behind the front worker.

### `option_positions.py`

Not in this folder, but closely related.

The actual option-position workflow now lives in `Python Scripts/trading_support/option_positions.py` plus its split internal helpers:

- `_option_positions_market.py`
- `_option_positions_snapshot.py`
- `_option_positions_strategy.py`
- `_option_positions_state.py`
- `_option_positions_manager.py`
- `_option_positions_momentum.py`
- `_option_positions_defaults.py`

`options/position_manager.py`, `options/option_dte_buckets.py`, `options/option_momentum.py`, `options/option_position_state.py`, and `core/trading_config.py` all exist partly to support that workflow cleanly from the service layer.

## Which Modules Are Safe To Import Directly?

Most reusable imports come from:

- `core/trading_config.py`
- `core/env_parsing.py`
- `core/numbers.py`
- `core/runtime_paths.py`
- `options/option_dte_buckets.py`
- `options/option_momentum.py`
- `options/option_position_state.py`
- `trading/order_candidates.py`
- `trading/trading_gateway.py`
- `trading/trade_executor.py`
- `options/position_manager.py`

The modules most tied to the live runtime are:

- `runtime/worker_application.py`
- `runtime/cold_start.py`
- `trading/trade_journal.py`

## Practical Import Examples

```python
from services.core.trading_config import WorkerPaths, WorkerSettings
from services.options.option_dte_buckets import resolve_dte_bucket_for_days
from services.trading.order_candidates import OrderCandidateBuilder
from services.trading.trading_gateway import AlpacaTradingGateway
from services.trading.trade_executor import OptionTradeExecutor
from services.options.position_manager import OptionPositionManagerService
```

## Notes

- Functions or methods that start with `_` are internal helpers even if they are technically importable.
- The service layer tries to return plain dictionaries for runtime payloads so the dashboard, API, and worker scripts can share them easily.
- When changing option DTE buckets, start with `options/option_dte_buckets.py`; the rest of the service layer is now wired to follow that shared source of truth.
