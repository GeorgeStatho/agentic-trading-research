---
name: verify
description: Run this repo's operator CLIs and API against a snapshot of the live DB to verify a change at runtime, without paid model calls or broker access.
---

# Verify a change in Stock-trading-experiment

## Never
- Point anything at the live DB (`~/stock-restore/db-dev/stock_experiment.db`): it is production.
- Read `.env`, or run anything that loads it with keys. The worktree has no `.env`, so `load_project_env` and
  `scripts/export_alpaca_trade_history.load_env_file` load nothing there. Check with `test -e .env` first.
- Run the news collector, the worker, `decision_pipeline`, or `research.py forward-cycle` with a challenger
  on: they make paid model calls or broker requests.

## Setup
```bash
W=$PWD
export PYTHONPATH="$W:$W/Data:$W/Python Scripts:$W/Python Scripts/Webscraping:$W/Python Scripts/agentCallers"
mkdir -p .verify && echo ".verify/" >> "$(git rev-parse --git-path info/exclude)"
# The snapshot is about 6 GB and takes about 13 min. /tmp is RAM-backed and too small, so write it inside the worktree.
python3 -c "import sqlite3; sqlite3.connect('file:~/stock-restore/db-dev/stock_experiment.db?mode=ro', uri=True).execute(\"VACUUM INTO '.verify/snapshot.db'\")"
```
The snapshot has production's schema. Commands that run `initialize_database` migrate the copy, not live.

## Surfaces (all safe on the snapshot)
- API, without the start-up schema setup or telemetry ping. `api.py`'s `__main__` hard-codes port 5000:
  `DB_PATH=$W/.verify/snapshot.db python3 -m flask --app api run --host 127.0.0.1 --port 5055`,
  then `curl http://127.0.0.1:5055/api/forward-evaluation`. It takes about 15 s when the snapshot is on `/mnt/e`
  (a 9p Windows mount), against about 0.5 s on ext4. That is the mount, not the code.
- `scripts/research.py --db .verify/snapshot.db discovery-ledger --days 30 [--events]` and `forward-status`.
- `scripts/v2_part4.py --db .verify/snapshot.db collect --cached-only`: catalyst responses plus
  `collect_active_runs`, with no network (about 5 min).
- `DISCOVERY_MODE=shadow scripts/discovery.py --db .verify/snapshot.db consume|scan|status`: admission only, no
  models.
- A new candidate's `pipeline_version_json.source_hashes` shows the trading-path file set.

## Cleanup
Stop the server, run `rm -rf .verify`, remove the exclude line, and run
`git checkout -- Data/stock_experiment.db-shm Data/stock_experiment.db-wal` (tests touch these tracked files).
