# Tests

This folder contains the project's automated Python test suite.

The tests here mainly protect the backend areas that were cleaned up in the refactor roadmap:

- sector and industry ranking logic
- strategist and manager payload contracts
- split-module regression coverage for pipeline and strategist helpers
- API startup and health-route smoke checks
- option-manager behavior and compatibility wrappers

## Layers

- `tests/test_*.py`: unit tests. One module or rule at a time, with mocked collaborators. Fast enough for Docker image builds.
- `tests/integration/`: several real stages against a throwaway database, with no network. `test_pipeline_to_api.py` runs ranking, admission, the strategist and the manager on seeded news, then reads the results back through the API.
- `tests/e2e/`: one whole worker trading cycle, from collected news to an order at a fake broker and the dashboard API. A plain run skips it. Run it with `test --e2e`, which CI also does.

Integration and e2e tests replace only the outside world, using `tests/integration/fakes.py`. That covers the Alpaca SDK clients, yfinance and the LLM call, and makes any real HTTP request fail the test. Prefer adding a case there over a mock-wired unit test, whenever the behaviour spans stages.

Run from the project root:

```bash
python3 scripts/project.py test                           # whole suite
python3 scripts/project.py test tests.test_exposure_map   # named modules
python3 scripts/project.py test --changed                 # only tests your edits affect
python3 scripts/project.py test --e2e                     # end-to-end trading cycle
```

`--changed` needs the dev extras (`pip install -e ".[dev]"`); see `docs/DEVELOPMENT.md`.

Evidence refresh integration: `tests/integration/test_evidence_refresh.py` exercises the durable
company-interest queue and collection-to-research handoff with synthetic providers.
Run with an isolated `DB_PATH` and `TELEMETRY_ENABLED=false`; no live providers are used.

Interest-driven discovery: `tests/integration/test_discovery_interest.py` verifies current
selection scopes, expiry, notifications, recovery scans, collection dependencies,
and exactly one successor investigation across discovery and continuation routes.
