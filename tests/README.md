# Tests

This folder contains the project's automated Python test suite.

The tests here mainly protect the backend areas that were cleaned up in the refactor roadmap:

- sector and industry ranking logic
- strategist and manager payload contracts
- split-module regression coverage for pipeline and strategist helpers
- API startup and health-route smoke checks
- option-manager behavior and compatibility wrappers

Most of these are lightweight unit tests with mocked dependencies so they run quickly and can be executed during Docker image builds.

Run from the project root:

```bash
python3 scripts/project.py test                           # whole suite
python3 scripts/project.py test tests.test_exposure_map   # named modules
python3 scripts/project.py test --changed                 # only tests your edits affect
```

`--changed` needs the dev extras (`pip install -e ".[dev]"`); see `docs/DEVELOPMENT.md`.

Evidence refresh integration: `tests/test_evidence_refresh.py` exercises the durable
company-interest queue and collection-to-research handoff with synthetic providers.
Run with an isolated `DB_PATH` and `TELEMETRY_ENABLED=false`; no live providers are used.

Interest-driven discovery: `tests/test_discovery_interest.py` verifies current
selection scopes, expiry, notifications, recovery scans, collection dependencies,
and exactly one successor investigation across discovery and continuation routes.
