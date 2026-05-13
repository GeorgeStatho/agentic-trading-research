# Refactor Roadmap

This document lays out a concrete, staged plan to improve the readability and maintainability of the codebase without trying to rewrite the whole system at once.

The intent is to make the code easier to navigate, safer to change, and simpler to test while preserving the current runtime behavior.

## Goals

- reduce confusion about which modules are canonical vs compatibility wrappers
- standardize bootstrap and environment-loading behavior
- shrink very large modules into clearer units of responsibility
- replace fragile nested dictionaries with typed data contracts over time
- add enough test coverage to make refactoring safe

## Phase 1: Stabilize Interfaces

Goal: make the codebase easier to navigate before changing behavior.

### Problems this phase addresses

- legacy wrappers and modern implementations both exist
- some public module names are misspelled and increase onboarding cost
- it is not always obvious where the "real" implementation lives

### Targets

- `Python Scripts/agentCallers/CompanyOppurtunist.py`
- `Python Scripts/agentCallers/industryIntrest.py`
- `Python Scripts/agentCallers/sectorIntrest.py`
- `Python Scripts/Webscraping/companyNewsPipeline.py`
- `Python Scripts/Webscraping/industryNewsPipeline.py`
- `Python Scripts/Webscraping/sectorNewsPipeline.py`
- `.env.example`

### Actions

1. Define canonical import paths for each subsystem.
2. Treat legacy wrappers as compatibility-only modules.
3. Add a short "Preferred Imports" section to the main docs.
4. Stop introducing new misspelled identifiers in code, docs, and env vars.
5. Add deprecation comments to wrapper modules that re-export from newer locations.

### Deliverables

- one documented canonical import map
- wrapper modules clearly labeled as compatibility surface
- no new references to misspelled module names

### Success criteria

- a new contributor can find the real implementation in one hop
- new code imports canonical modules only

## Phase 2: Centralize Bootstrap and Config

Goal: remove environment-loading and path-setup inconsistencies.

### Problems this phase addresses

- some modules load `.env` and others do not
- behavior can vary depending on which entrypoint imports a module first
- env parsing helpers are duplicated in multiple places

### Targets

- `Python Scripts/agentCallers/_paths.py`
- `Python Scripts/agentCallers/main.py`
- `Python Scripts/agentCallers/agent_pipeline/main.py`
- `Python Scripts/agentCallers/agent_helpers/manager.py`
- `Python Scripts/agentCallers/agent_stages/manager.py`
- `Python Scripts/services/config.py`
- `api.py`
- `Python Scripts/trading_support/option_positions.py`

### Actions

1. Decide one rule:
   Entry points load `.env`; library modules do not.
2. Use `_paths.bootstrap_agent_callers(...)` consistently for path setup only.
3. Move shared env parsing into common config helpers or typed settings objects.
4. Reuse the `services/config.py` pattern in non-service code where practical.
5. Remove duplicated `_load_env_*` helpers where they overlap.

### Deliverables

- one documented bootstrap policy
- fewer import-time side effects in library modules
- shared env/config parsing helpers

### Success criteria

- the same library module behaves consistently regardless of caller
- duplicated env parsing logic is significantly reduced

## Phase 3: Shrink the Biggest Files

Goal: reduce cognitive load and make edits smaller and safer.

### Problems this phase addresses

- several central modules are carrying too many responsibilities
- large diffs are hard to review
- it is difficult to test pieces in isolation

### Highest-priority files

- `api.py`
- `Python Scripts/agentCallers/agent_pipeline/main.py`
- `Python Scripts/agentCallers/agent_stages/strategist.py`
- `Python Scripts/agentCallers/agent_helpers/deterministic_option_selector.py`
- `Data/news_db.py`

### Suggested splits

#### `api.py`

Split into:

- `routes/health.py`
- `routes/dashboard.py`
- `routes/trades.py`
- `routes/news.py`
- shared API utility modules for JSON loading, Alpaca requests, and formatting helpers

#### `agent_pipeline/main.py`

Split into:

- `ranking.py`
- `target_selection.py`
- `cleanup.py`
- `existing_db_view.py`
- `news_refresh.py`

#### `agent_stages/strategist.py`

Split into:

- `prompt.py`
- `schema.py`
- `normalization.py`
- `runner.py`

#### `agent_helpers/deterministic_option_selector.py`

Split into:

- `normalization.py`
- `filters.py`
- `hybrid_strategy.py`
- `greeks_strategy.py`
- `selection.py`

### Rules for this phase

- preserve behavior first
- do not mix file extraction with logic changes
- add tests or snapshots before large moves when possible

### Deliverables

- central modules reduced to clearer responsibilities
- files trending toward 300-500 LOC where practical

### Success criteria

- each module has one primary reason to change
- reviews become smaller and more focused

## Phase 4: Introduce Typed Data Contracts

Goal: replace loose nested payload dictionaries with explicit shapes.

### Problems this phase addresses

- many subsystem boundaries use `dict[str, Any]`
- payload shapes are hard to discover
- refactors are brittle because key names are implicit

### Targets

- `Python Scripts/agentCallers/agent_builders/opportunist_payload.py`
- `Python Scripts/agentCallers/agent_builders/company_opportunity.py`
- `Python Scripts/agentCallers/agent_builders/strategist_payload.py`
- `Python Scripts/agentCallers/agent_helpers/manager.py`
- `Python Scripts/agentCallers/agent_pipeline/main.py`

### Actions

1. Add `TypedDict` or dataclass contracts for:
   - sector ranking output
   - industry ranking output
   - company selection payloads
   - strategist payload
   - manager payload
   - company opportunist summary
2. Introduce contracts at subsystem boundaries first.
3. Keep JSON serialization stable while improving internal typing.
4. Gradually reduce `dict[str, Any]` signatures in high-value paths.

### Deliverables

- contract modules or `types.py` files per subsystem
- clearer function signatures
- better IDE/editor support

### Success criteria

- common payload shapes are explicit and reusable
- fewer bugs caused by key mismatches or undocumented fields

## Phase 5: Add Refactor Safety Tests

Goal: make maintainability improvements safer to ship.

### Problems this phase addresses

- low automated coverage in high-churn areas
- refactors require too much manual verification
- payload regressions are hard to catch early

### Targets

- ranking helpers
- payload builders
- deterministic option selection
- existing-DB pipeline view
- DB-backed summary helpers

### Actions

1. Add ranking tests for sector/industry windows and ordering.
2. Add payload contract tests for strategist and manager builders.
3. Add fixture-driven tests for deterministic option selection.
4. Add a smoke test for `run_agent_pipeline_from_existing_data()` using a tiny seeded SQLite database.
5. Keep `Data/db_test.py` as a diagnostic tool if useful, but add assertion-based tests in a proper test suite.

### Suggested test directories

- `tests/agent_pipeline/`
- `tests/agent_builders/`
- `tests/agent_helpers/`
- `tests/option_selector/`

### Deliverables

- repeatable tests for core pipeline behavior
- seed fixtures for small DB-backed scenarios

### Success criteria

- core refactors can be validated without full manual end-to-end runs
- payload and ranking regressions are caught automatically

## Recommended Order

1. Phase 1: Stabilize Interfaces
2. Phase 2: Centralize Bootstrap and Config
3. Phase 5: Add tests for ranking and payload contracts
4. Phase 3: Split `agent_pipeline/main.py`
5. Phase 4: Introduce typed contracts in pipeline and builder boundaries
6. Phase 3: Split `strategist.py`, `api.py`, and `deterministic_option_selector.py`

## Suggested First Sprint

If the goal is high impact with relatively low risk, start here:

1. Document canonical modules and mark wrappers as compatibility-only.
2. Standardize env-loading rules and centralize repeated config parsing.
3. Add ranking-window and existing-DB pipeline tests.
4. Split `agent_pipeline/main.py` into smaller concern-based modules.

This sequence improves navigation, reduces configuration surprises, and creates a safer base for larger refactors later.

## Notes

- Prefer behavior-preserving refactors first.
- Keep compatibility wrappers working until callers have migrated.
- Separate "move code" changes from "change behavior" changes whenever possible.
- Use the newer `services/config.py` style as a model for typed settings and path organization.
