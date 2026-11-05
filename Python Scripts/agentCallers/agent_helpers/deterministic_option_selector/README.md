# deterministic_option_selector package

This package owns deterministic option contract selection after the manager stage has already decided direction.

Public entrypoint:

- `from agent_helpers.deterministic_option_selector import apply_deterministic_option_selection`

Current structure:

- `__init__.py`
  Stable facade, mode dispatch, and final recommendation orchestration.
- `config.py`
  Selector env/config loading and tunables.
- `normalize.py`
  Decision/confidence normalization and selection guardrails.
- `market.py`
  Read-only helpers for contract quotes, greeks, DTE, and market-context access.
- `volatility.py`
  IV percentile, IV/HV, and term-structure scoring.
- `filters.py`
  Shared DTE/liquidity/OTM filters and ranking helpers.
- `debug.py`
  Contract debug snapshots and rejection-reason helpers.
- `simple.py`
  Simple mode selection path.
- `hybrid.py`
  Hybrid mode selection path.
- `greeks.py`
  Greeks/swing mode selection path.
- `__main__.py`
  Package manual test entrypoint.

High-level flow:

1. Normalize the manager recommendation.
2. Check directional/confidence guardrails.
3. Dispatch to the configured selector mode.
4. Score the chosen contract with volatility-aware penalties.
5. Keep manager confidence intact and write the adjusted value to `confidence_after_volatility`.
6. Return the enriched recommendation plus `selection_debug`.

Notes:

- The facade still exposes `OPTION_SELECTOR_MODE` and related tunables because tests currently patch the package directly.
- The selector is responsible for contract-level viability, not for deciding trade direction.
- After the manager decides, `MANAGER_OPTION_SELECTION_LIMIT_PER_TYPE` (default 30) contracts per type are fetched for the selector only.
- Hybrid mode's gamma floor is per 1% stock move (`gamma * spot / 100 >= HYBRID_MIN_GAMMA_PER_PCT_MOVE`, 0.01), so high-priced stocks are not rejected for their naturally small raw gamma. Raw `HYBRID_MIN_GAMMA` applies only when the stock price is unknown.
- The minimum-OTM-distance floor is off (`HYBRID_REQUIRE_MIN_OTM_DISTANCE = False`). OTM distance now only ranks candidates, because the hard floor dropped the most liquid strikes.
- When a DTE bucket's window holds only weeklies, `get_dte_bucket_selection_range` (`services/options/option_dte_buckets.py`) extends it to the next monthly expiration, if that's at most 7 days past the window. Weeklies often have almost no open interest.
