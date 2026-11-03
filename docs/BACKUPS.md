# Database backups and restore

The live database is backed up two ways, both onto the E: drive (`/mnt/e/StockBackups`), which is a different disk from the WSL disk that holds the database:

| What | Tool | Where | Covers |
| --- | --- | --- | --- |
| Point-in-time recovery | Litestream sidecar, [config/litestream.yml](../config/litestream.yml) | `litestream/stock_experiment/` | Any second in the last 7 days. Each change is copied about a second after it's written. |
| Nightly full copy | `db_backup`, [Python Scripts/db_backup_main.py](../Python%20Scripts/db_backup_main.py) | `stock_experiment.latest.db` plus `stock_experiment.latest.json` | One plain SQLite file, replaced every day at 05:00 America/New_York |

## How the nightly copy works

1. `VACUUM INTO` writes a consistent, compacted copy to `.backup-tmp/`, next to the live database. Writers keep going, because the copy is one read transaction under WAL.
2. `PRAGMA quick_check` checks the copy.
3. The copy goes onto E: as `stock_experiment.latest.db.tmp` and is then renamed over `stock_experiment.latest.db`.

A failed step leaves the previous backup untouched and retries an hour later. The manifest records the backup's date, sizes, check result and a hash of the schema.

## Health

`db_backup` writes a status file that the dashboard shows as **DB Backup**. `/api/health/components` returns 503 when this component is unhealthy, so Uptime Kuma alerts on it. The component turns to error when:

- the last nightly copy is more than 26 hours old (`DB_BACKUP_MAX_AGE_HOURS`), or the last attempt failed;
- the Litestream replica hasn't started, or trails the last database write by more than 10 minutes (`DB_BACKUP_MAX_REPLICA_LAG_SECONDS`).

## Running it

```bash
STOCK_DB_DIR=~/stock-restore/db-dev docker compose \
  -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.backup.yml up -d litestream db_backup api
# Take a backup now instead of waiting for 05:00:
STOCK_DB_DIR=~/stock-restore/db-dev docker compose \
  -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.backup.yml run --rm db_backup \
  python "Python Scripts/db_backup_main.py" --once
```

`STOCK_DB_DIR` must be the folder the other services mount at `/var/lib/stock-experiment`. Without it, the overlay falls back to the `db_data` named volume used by `docker-compose.yml` alone. `BACKUP_DIR` overrides the E: drive folder.

## Restore

Stop everything that writes the database first, then swap the file:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.backup.yml \
  stop worker option_manager news_collector marketdata_eod_sync api db_backup litestream
```

**From the nightly copy:**

```bash
cd ~/stock-restore/db-dev
mv stock_experiment.db stock_experiment.db.replaced && rm -f stock_experiment.db-wal stock_experiment.db-shm
cp /mnt/e/StockBackups/stock_experiment.latest.db stock_experiment.db
```

**To a point in time** (UTC, inside the last 7 days):

```bash
STOCK_DB_DIR=~/stock-restore/db-dev docker compose \
  -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.backup.yml run --rm litestream \
  restore -timestamp 2026-11-03T15:00:00Z -o /var/lib/stock-experiment/restored.db \
  /var/lib/stock-experiment/stock_experiment.db
cd ~/stock-restore/db-dev
sqlite3 restored.db "PRAGMA quick_check"   # expect: ok
mv stock_experiment.db stock_experiment.db.replaced && rm -f stock_experiment.db-wal stock_experiment.db-shm
mv restored.db stock_experiment.db
```

The restore writes next to the live database, on the Linux disk, because SQLite is unreliable on the E: drive's 9p mount.

Leave out `-timestamp` to restore the latest replicated state. Start the stack again with `up -d`. Litestream sees the replaced file and begins a new generation. Delete `stock_experiment.db.replaced` once the restored database looks right.

**Drill:** once a month, restore to a time a few minutes ago into `/var/lib/stock-experiment/restored.db` without swapping it in. Check it with `quick_check` and a recent-rows query, then delete it. That proves the replica can actually be restored. The services can keep running during a drill.

## With database compaction

[scripts/compact_db.py](../scripts/compact_db.py) ends in a `VACUUM`, which rewrites the whole file through the WAL. Stop `litestream` before running it and start it afterwards. Litestream then begins a new generation from the compacted file, and the 7-day window counts from that point.
