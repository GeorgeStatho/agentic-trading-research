# Stock-trading-experiment

An options paper-trading bot: news is collected and classified, a strategist and a manager model pick
calls or puts, entry gates (mostly in shadow mode) check them, the option manager handles exits through
Alpaca, and a web dashboard plus research tooling measure the results. Runs as a Docker Compose stack.

## Read first

- `docs/README.md`: index of all documents. `docs/CODE_MAP.md` maps the repo;
  `docs/ARCHITECTURE.md` says where new code belongs.
- `docs/ALPACA.md` before calling Alpaca: most broker and market-data operations already have a wrapper.
- `docs/FEATURE_PLAYBOOK.md` before adding a gate, a persisted feature, an LLM stage or a dashboard page.
- `docs/DEVELOPMENT.md` for installing, import roots and tests.
- The repo is indexed by graft (`graft ask`, `graft grep`, `graft callers`); search for an existing helper
  before writing one.

## Commands

```bash
python3 scripts/project.py test                 # whole suite
python3 scripts/project.py test tests.test_x    # selected modules
python3 scripts/project.py test --changed       # tests affected by edits (dev extras)
cd web_dashboard && npm test && npm run lint && npm run build
python3 -m ruff check .                         # also run by tests/test_static_checks.py
```

## Rules

- **Tests first**: write the test, see it fail, then implement.
- **Never point anything at the live database** (`~/stock-restore/db-dev/stock_experiment.db`)
  when verifying a change. Work on a copy (`sqlite3 ... ".backup .verify/<your-name>.db"`). Backtests are the
  exception: they are a feature meant to run on live.
- **`.verify/` is shared** with other sessions and scripts. Delete only files you created; never `rm -rf .verify`.
- **The main checkout has a `.env` with broker and model keys.** Do not run CLIs or entrypoints that load it
  (worker, news collector, `decision_pipeline`, `research.py forward-cycle` with a challenger): they make paid
  model calls or broker requests. Verify by calling functions directly.
- Schema changes are new idempotent steps in `Data/market_db_modules/bootstrap.py` `_setup_steps`; regenerate
  `tests/fixtures/fresh_schema.json` (`UPDATE_SCHEMA_SNAPSHOT=1`, see `tests/test_bootstrap_steps.py`).
- New entry gates start in shadow mode and are enforced only through a promotion proof
  (`services/research/research_promotion.py`).
- Keep `.env.example` in step with new settings, with a comment on each.

## Commits

One commit per feature or fix, with the message prefixed by its kind and written as a sentence about the
behaviour: `FEAT:`, `FIX:`, `TEST:`, `REFACTOR:`, `DOC:`. Example:
`FIX:The stop-loss confirms on the stock's move from the recorded entry price`.
