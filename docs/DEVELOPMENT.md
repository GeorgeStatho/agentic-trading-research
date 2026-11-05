# Development

## Install

The project is an application run from its checkout. Install it editable into your
Python 3.11+ environment:

```bash
pip install -e .
```

This installs the dependencies in `requirements.txt` and registers the import roots
declared in `pyproject.toml` (`.`, `Data`, `Python Scripts`, `Python Scripts/agentCallers`
and `Python Scripts/Webscraping`). Those roots are the only import configuration. Modules
must not edit `sys.path`, and new code imports through them, for example
`from services.core.trading_config import ...` or `from agent_stages.strategist_runner import ...`.

If an import fails with `ModuleNotFoundError` for a project module, the checkout is
not installed in the active environment. Run `pip install -e .` again.

Import at module level. Function-level imports remain only where they are needed:

- to break a real import cycle, for example the data-layer bootstrap and its migrations,
  or the Webscraping `engine`/`extractors` pair;
- where tests substitute a module or patch its attribute, so the call must look it up late;
- for optional or heavy third-party dependencies;
- in `scripts/` CLIs that set `DB_PATH` from `--db` before importing the data layer.

## Tests

One command runs the whole suite:

```bash
python3 scripts/project.py test
python3 scripts/project.py test tests.test_thesis_evidence_contract   # selected modules
```

With the dev extras installed (`pip install -e ".[dev]"`), the whole suite runs in
parallel through `pytest -n auto`; without them it runs one test at a time through
`unittest`. Selected modules always run through `unittest`. Tests are written as
`unittest` test cases either way.

While you work, run only the tests your edits affect:

```bash
python3 scripts/project.py test --changed
```

It uses `pytest-testmon` from the dev extras, which records the code each test executes and
re-runs only tests whose executed code changed since the previous `--changed` run, plus any
that failed last time. The first run, and any run after a change to the schema, configuration
or dependency files (`*.sql`, `config/`, `Data/*.json`, `.env.example`, `pyproject.toml`,
`requirements.txt`), runs every test and re-records, because those files are read rather than
executed and cannot be traced to individual tests. The map lives in `.testmondata` and is
ignored by git. Run the whole suite before committing; the image build always does.

Keep the checkout on the Linux filesystem when you work in WSL (for example under
`~/code`, not `/mnt/c` or `/mnt/e`). Every file access on a Windows drive goes through a
slow file-sharing layer, and the suite runs about five times slower there.

Tests are hermetic. `tests/__init__.py` runs before any test module and:

- removes every setting listed in `.env.example` from the environment, so tests use
  code defaults whatever your shell or `.env` contains;
- points `DB_PATH` at a throwaway database, initialized once per run (once per worker
  process in a parallel run);
- disables telemetry;
- points the model knowledge-cutoff table at `tests/fixtures/default_model_cutoffs.csv`, which lists only the
  generic `local` model.

Test modules must not load `.env`, stub installed packages in `sys.modules`, or rely on
another test having run first. Import shared fixtures as `from tests.<module> import ...`.
The worker Docker image runs the same command during `docker build`, without the dev
extras.

A test that simulates a database left by older code must also clear `schema_bootstrap`.
Otherwise the market setup sees its own version stamp and skips the migrations (see
[ARCHITECTURE.md](ARCHITECTURE.md)).

## Configuration

Settings are read once per process into one typed object, `services.core.settings.Settings`
(models and Ollama hosts per stage, Vertex retry and cache, manager-context limits,
opportunist limits and the continuation policy).

- Entrypoints call `load_process_settings()` under `if __name__ == "__main__":`. It loads
  `.env` and then reads the environment once. Importing a module never loads `.env` and
  never reads configuration.
- Library code calls `get_settings()` when it runs, for example
  `get_settings().models.strategist.model`. Function defaults are `None` and resolve from
  settings, so an explicit argument still wins.
- Tests inject values with `use_settings(...)`, for example
  `use_settings(manager_context=replace(get_settings().manager_context, iv_history_path=path))`.
- The pipeline manifest records the loaded object under `process_settings`, so a decision's
  manifest shows the settings it actually used.

Add a new setting to the relevant section in `services/core/settings.py` and to `.env.example`;
the test bootstrap uses `.env.example` to isolate tests from local configuration.

Not yet on `Settings`: the option exit settings (`services.core.trading_config.OptionPositionSettings`,
read at call time), the process composition roots in
`Python Scripts/main.py` and `api_support/context.py` (built from the typed settings
classes in `services/core/trading_config.py`), and `DB_PATH`, which the data layer reads when
`db_common` is imported.

## Entry gates and pipeline contracts

Entry gates are listed once in `services/gates/entry_gates.py`. Order eligibility, selected-option
summaries, decision history and research inputs iterate that registry, so adding a gate means
one registry entry plus the code that computes its verdict. The strategist result, manager
result, gate verdict and order candidate are TypedDicts in
`Python Scripts/agentCallers/agent_contracts.py`. `tests/test_pipeline_contracts.py` fails when
a model schema, its prompt example and its TypedDict disagree, or when a registered gate is
not carried through to orders.

## Entrypoints

```bash
python3 scripts/project.py run api
python3 scripts/project.py run worker
python3 scripts/project.py run news
python3 scripts/project.py run option-manager
```

See [scripts/README.md](../scripts/README.md) for the research and V2 maintenance CLIs,
and `docker compose up --build` for the integrated stack.
