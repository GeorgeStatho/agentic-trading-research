# Feature Playbook

Use this checklist when adding a feature so implementation remains discoverable and auditable.

## Read-Only Dashboard Feature

1. Add the data-shaping use case in `api_support/<feature>.py`.
2. Add a thin route in `api_routes/<feature>.py` and register it in `api.py`.
3. Add a focused `tests/test_<feature>.py` module.
4. Add a page or component plus matching CSS in `web_dashboard/src/`.
5. Update `README.md` and `CODE_MAP.md` only when the user-visible architecture changes.

## Persisted Feature

1. Add tables and indexes to the appropriate schema file with idempotent SQL.
2. Add database functions under the correct `*_db_modules/` package.
3. Re-export stable functions through `market_db.py`/`news_db.py` and `db_helpers`.
4. Test empty databases, invalid input, and read-after-write behavior.

## LLM-Assisted Feature

1. Build a minimal, dated, grounded payload from persisted data.
2. Request structured JSON with a schema.
3. Validate every returned ID, symbol, and citation against the input payload.
4. Persist model name, prompt version, input IDs, output, and timestamp.
5. Keep scoring, constraints, and any order permission in deterministic Python.

## Entry Gate

1. Compute the verdict in a `services/` module and return a dict with `allowed`, `would_allow`,
   `reason_codes`, `policy_version` and `enforcement_mode`.
2. Add one `EntryGate` to `services/entry_gates.py`; eligibility and carrying follow from it.
3. Record it with `record_gate` so research reports can measure it, and add its policy block to
   `gate_config` in `services/pipeline_version.py` and `GATE_BLOCKS` for promotion proofs.
4. Default to shadow; enforcement stays behind `guard_policy` and a matching promotion proof.

## Demo Feature

1. Put static fixtures in `api_support/demo_mode.py` or a feature-local equivalent.
2. Make fixtures explicit about being fictional and read-only.
3. Never require database data, broker credentials, or LLM credentials to render the demo.
4. Add a smoke test for the fixture and API route.

## Validation Before Handoff

```bash
python3 scripts/project.py test tests.test_<feature>
python3 scripts/project.py dashboard build
```

Run broader targeted tests when a feature crosses persistence, API, agent, or execution boundaries.
