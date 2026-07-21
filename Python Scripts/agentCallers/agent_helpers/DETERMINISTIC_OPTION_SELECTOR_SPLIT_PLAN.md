# Deterministic Option Selector Split Plan

This document lays out a practical split plan for `deterministic_option_selector.py`.

Current state:

- File: `agent_helpers/deterministic_option_selector.py`
- Current size: about `1986` LOC
- Current responsibility mix:
  - env/config loading
  - normalization helpers
  - confidence / guardrail logic
  - market-context accessors
  - volatility scoring
  - DTE / liquidity / OTM filtering
  - sort / debug helpers
  - simple mode selector
  - hybrid mode selector
  - greeks mode selector
  - final orchestration / recommendation mutation

The goal is not to create tiny fragments for their own sake. The goal is:

- each module should have one main reason to change
- most modules should land around `150-250` LOC
- the public import surface should stay stable
- test patching should keep working during the migration

## Current Functional Map

The current file roughly breaks down like this:

1. Config and tunables
   - env readers
   - selector mode
   - tunable constants for simple / hybrid / greeks paths

2. Shared normalization and guardrails
   - normalize decision / confidence / strategist decision
   - confidence ranking and downgrade logic
   - selection eligibility checks

3. Contract and market-context accessors
   - quote / greeks / DTE helpers
   - reference stock price lookup
   - market-context volatility lookups

4. Volatility scoring
   - IV percentile
   - IV/HV
   - term structure warning
   - total volatility assessment

5. Shared filtering and ranking
   - DTE bucket matching
   - spread / theta / OTM checks
   - fallback-side validation
   - sort key and debug snapshot helpers

6. Mode-specific selection implementations
   - simple mode
   - hybrid mode
   - greeks mode

7. Final orchestration
   - dispatch to selection mode
   - selected-option volatility gating
   - confidence-after-volatility handling
   - final recommendation enrichment

## Recommended Target Structure

Keep `deterministic_option_selector.py` as the stable facade and public entrypoint.

Recommended split:

### `deterministic_option_selector.py`

Target size: `120-220` LOC

Purpose:

- public facade
- stable import surface
- final `apply_deterministic_option_selection(...)`
- compatibility exports for tests and callers

What stays here:

- `SELECTOR_VERSION`
- `apply_deterministic_option_selection(...)`
- possibly `_pick_matching_contract(...)` if you want dispatch close to the entrypoint
- compatibility wrappers / re-exported constants

Why:

- other code already imports this file directly
- tests patch constants like `OPTION_SELECTOR_MODE` on this module today
- keeping a facade avoids a disruptive migration

### `_selector_config.py`

Target size: `120-180` LOC

Purpose:

- env readers
- selector mode
- global tunables

Move here:

- `_env_flag(...)`
- `_env_str(...)`
- `OPTION_SELECTOR_MODE`
- `ALLOW_RISKY_SIMPLE_FALLBACK`
- all simple/hybrid/greeks constants
- `MIN_TARGET_OTM_DOLLARS`
- `MAX_TARGET_OTM_DOLLARS`
- `SHORT_TERM_EVENT_PRICING_DTE`
- `NEAR_TERM_EVENT_PRICING_DTE`
- `VOLATILITY_CONFIDENCE_LOWEST_ALLOWED`

Important compatibility note:

- the facade should re-export these names
- if tests still patch `deterministic_option_selector.OPTION_SELECTOR_MODE`, the facade should be the value source used by orchestration
- do not silently move patch-sensitive state without a compatibility layer

### `_selector_normalize.py`

Target size: `120-200` LOC

Purpose:

- normalization and policy-level gating

Move here:

- `_coerce_float(...)`
- `_normalize_option_id(...)`
- `_normalize_decision(...)`
- `_normalize_confidence(...)`
- `_confidence_rank(...)`
- `_downgrade_confidence(...)`
- `_normalize_strategist_decision(...)`
- `_coerce_bool(...)`
- `_is_selection_eligible_under_confidence_guardrails(...)`

### `_selector_market.py`

Target size: `150-220` LOC

Purpose:

- read-only extraction from contracts and market context

Move here:

- `_resolve_target_otm_distance(...)`
- `_normalize_contract_type(...)`
- `_get_reference_stock_price(...)`
- `_get_latest_quote(...)`
- `_get_bid_price(...)`
- `_get_ask_price(...)`
- `_get_contract_market_price(...)`
- `_get_greek(...)`
- `_get_dte(...)`
- `_normalize_percentile(...)`
- `_get_contract_implied_volatility(...)`
- `_get_option_market_volatility_summary(...)`
- `_get_underlying_historical_volatility(...)`
- `_get_contract_iv_percentile(...)`

### `_selector_volatility.py`

Target size: `150-230` LOC

Purpose:

- contract-level volatility scoring only

Move here:

- `_score_iv_percentile(...)`
- `_score_iv_hv_ratio(...)`
- `_score_term_structure_warning(...)`
- `_volatility_penalty_to_confidence_steps(...)`
- `_assess_contract_volatility(...)`

Notes:

- this module should depend on `_selector_market.py` and `_selector_config.py`
- it should not know anything about simple vs hybrid vs greeks selection

### `_selector_filters.py`

Target size: `180-260` LOC

Purpose:

- cross-mode contract validation and ranking helpers

Move here:

- `_resolve_dte_range(...)`
- `_contract_matches_target_dte_bucket(...)`
- `_get_spread_pct(...)`
- `_get_theta_to_price(...)`
- `_is_otm_contract(...)`
- `_is_one_dollar_otm_contract(...)`
- `_meets_min_otm_distance(...)`
- `_is_valid_fallback_side_contract(...)`
- `_otm_distance(...)`
- `_distance_to_target_otm(...)`
- `_basic_sort_key(...)`

This module may end up slightly above 200 LOC. That is acceptable if it still feels cohesive.

### `_selector_debug.py`

Target size: `150-220` LOC

Purpose:

- debug payload assembly only

Move here:

- `_contract_debug_snapshot(...)`
- `_debug_contract_with_reasons(...)`
- `_build_simple_rejection_reasons(...)`

If debug helpers keep growing, this split will pay off quickly.

### `_selector_simple.py`

Target size: `180-260` LOC

Purpose:

- simple mode implementation

Move here:

- `_pick_matching_contract_simple(...)`

Potential local helpers:

- if simple mode has tiny internal-only helpers, colocate them here instead of polluting shared modules

### `_selector_hybrid.py`

Target size: `220-320` LOC

Purpose:

- hybrid mode implementation

Move here:

- `_passes_hybrid_fast_filters(...)`
- `_hybrid_fast_score(...)`
- `_pick_matching_contract_hybrid(...)`

This one may reasonably exceed 200 LOC because it contains a full selection path. That is still much better than keeping everything in one file.

### `_selector_greeks.py`

Target size: `220-320` LOC

Purpose:

- greeks/swing mode implementation

Move here:

- `_passes_short_swing_filters(...)`
- `_short_swing_score(...)`
- `_pick_matching_contract_greeks(...)`

### `_selector_dispatch.py`

Target size: `100-180` LOC

Purpose:

- mode routing and shared contract-pick wrapper

Move here:

- `_pick_matching_contract(...)`

This module should choose which mode implementation to call and return normalized selection/debug output.

## Recommended Dependency Rules

To avoid circular imports, use this dependency shape:

1. `_selector_config.py`
   - no selector-module dependencies

2. `_selector_normalize.py`
   - may depend on `_selector_config.py`

3. `_selector_market.py`
   - may depend on `_selector_config.py`
   - should not depend on mode modules

4. `_selector_volatility.py`
   - may depend on `_selector_market.py`
   - may depend on `_selector_config.py`

5. `_selector_filters.py`
   - may depend on `_selector_market.py`
   - may depend on `_selector_volatility.py`
   - may depend on `_selector_config.py`

6. `_selector_debug.py`
   - may depend on `_selector_market.py`
   - may depend on `_selector_volatility.py`

7. Mode modules
   - `_selector_simple.py`
   - `_selector_hybrid.py`
   - `_selector_greeks.py`
   - may depend on config / normalize / market / filters / debug / volatility
   - should not import the facade

8. `_selector_dispatch.py`
   - may depend on mode modules

9. `deterministic_option_selector.py`
   - facade only
   - imports dispatch/orchestration pieces
   - no other selector module should import this facade

## Migration Strategy

Do this in safe steps rather than one huge move.

### Phase 1: Extract pure helper modules

Low-risk first:

- `_selector_config.py`
- `_selector_normalize.py`
- `_selector_market.py`

Why first:

- these functions are mostly pure
- they are easier to move without changing behavior

### Phase 2: Extract volatility and shared filters

Next:

- `_selector_volatility.py`
- `_selector_filters.py`
- `_selector_debug.py`

Why second:

- these have more cross-calls
- after Phase 1 the dependency graph becomes clearer

### Phase 3: Extract mode implementations

Then:

- `_selector_simple.py`
- `_selector_hybrid.py`
- `_selector_greeks.py`

Why third:

- by this point most shared helpers are already stable
- mode files can become much smaller and easier to reason about

### Phase 4: Reduce facade to orchestration

Finally:

- move `_pick_matching_contract(...)` into `_selector_dispatch.py`
- leave `apply_deterministic_option_selection(...)` in the facade

At the end, the top-level file should mostly:

- import config
- call dispatch
- apply final volatility gating
- return the final enriched payload

## Recommended Testing Guardrails

Before splitting, pin the current behavior with tests around:

- contract selection per mode
- volatility-driven confidence downgrade behavior
- selected option source naming
- guardrail rejection reasons
- DTE bucket matching
- fallback-side handling
- debug payload shape

After each extraction step:

- run `test_deterministic_option_selector_volatility.py`
- run any existing selector and manager integration tests
- run `py_compile` on the new selector helper modules

## Compatibility Requirements

These should stay stable during the split:

- `apply_deterministic_option_selection(...)`
- `SELECTOR_VERSION`
- any public caller imports of `deterministic_option_selector`

These may also need facade compatibility if tests or scripts patch them:

- `OPTION_SELECTOR_MODE`
- `ALLOW_RISKY_SIMPLE_FALLBACK`
- other mode tunables if they are patched directly

If patching these values on the facade must keep working, there are two good options:

1. The facade remains the canonical location for patch-sensitive constants and passes them down.
2. The facade re-exports config values and orchestration reads from the facade, not directly from a child module.

Do not move patch-sensitive configuration into child modules and then forget that tests may still patch the old names.

## Suggested End State

An end state that would feel healthy:

- `deterministic_option_selector.py`
  public API + orchestration only
- `_selector_config.py`
  env + tuning constants
- `_selector_normalize.py`
  normalization + confidence utilities
- `_selector_market.py`
  market / contract accessors
- `_selector_volatility.py`
  volatility scoring
- `_selector_filters.py`
  shared filter + ranking logic
- `_selector_debug.py`
  debug payload assembly
- `_selector_simple.py`
  simple mode
- `_selector_hybrid.py`
  hybrid mode
- `_selector_greeks.py`
  greeks mode
- `_selector_dispatch.py`
  mode dispatch

That gives you modules with clearer ownership while preserving the existing deterministic-selector concept and public entrypoint.

## Short Recommendation

If we actually implement this, the safest order is:

1. extract config
2. extract normalize + market helpers
3. extract volatility + filters
4. extract simple mode
5. extract hybrid mode
6. extract greeks mode
7. reduce the top-level file to facade + orchestration

That sequence minimizes the chance of breaking behavior while steadily reducing the size of the main file.
