# Live server setup for the research features

A short checklist for turning on research Phases 1–7 on the Google Cloud VM (`instance-east`,
compose directory `~/agentic-trading-research`). Run steps 2–7 in the VM's SSH window. See the
[operator guide](RESEARCH_GUIDE.md) for what each feature does.

Everything here is shadow: it records what it would do and places no orders.

## 1. Push the code (on your computer)

```bash
git push origin main
```

## 2. Check the disk

```bash
df -h /
```

You need room for a compressed backup and a rebuilt image. If less than about 5 GB is free, clear
old images first: `docker image prune -f`.

## 3. Stop the stack and back up the database

```bash
cd ~/agentic-trading-research
docker compose stop worker news_collector option_manager marketdata_eod_sync api
docker compose run --rm --no-deps worker sh -c 'gzip -c "$DB_PATH" > /var/lib/stock-experiment/backup-before-research.db.gz'
```

Stopping first makes the copy consistent. The backup stays on the database volume next to the
database.

## 4. Pull the new code

```bash
git pull
```

## 5. Edit the settings

```bash
nano .env
```

First check the daily thesis validator. Your server's `.env` sets `shadow`, which records verdicts
without acting on them; `live` would let them close or trim positions, at most 2 a day. The last
database snapshot recorded its runs as `live`, so if you changed the value since, make sure the
containers were restarted.

```dotenv
THESIS_VALIDATOR_MODE=shadow
```

Then add, or change, these lines:

```dotenv
# Phase 1: SEC filings and facts. Use your own app name and a contact email you read.
COMPANY_EVIDENCE_ENABLED=true
SEC_USER_AGENT=StockExperiment you@example.com

# Phase 2: event discovery. This also feeds Phase 6 event reviews of open positions.
DISCOVERY_MODE=shadow
EVENT_THESIS_REVIEW_MODE=shadow

# Phase 7: rolling forward cohorts and closing marks.
FORWARD_EVALUATION_MODE=shadow

# Leave these off for now (see "Later" below).
RESEARCH_MODE=disabled
RESEARCH_CHALLENGER_MODE=off
```

`EVIDENCE_THESIS_MODE` and `TRADE_ECONOMICS_MODE` already default to `shadow`.

## 6. Rebuild and start

```bash
docker compose build
docker compose up -d
docker image prune -f
```

The database migrates on the first start (about 3 s on a copy of this database).

## 7. Check it works

Right after the start:

```bash
docker compose ps
docker compose logs --since 15m worker news_collector option_manager | grep -iE "traceback|error" | tail -20
```

After the first trading cycle:

```bash
docker compose exec worker python scripts/research.py forward-status
```

Expect a production cohort with enrolled candidates.

After the first collection cycle (up to 3 hours):

```bash
docker compose exec news_collector python scripts/discovery.py status
docker compose logs --since 3h news_collector | grep -iE "company_evidence|configuration_required"
```

After the next close, `forward-status` should list a `complete` mark session for that day.

The dashboard's **Research → V2 Rollout** page shows the same reading.

## Keep an eye on

**Disk.** The database grew about 1.9 GB a week before these features. Check weekly:

```bash
df -h /
docker compose exec worker ls -lh /var/lib/stock-experiment/
```

**Model calls.** Event reviews add up to 3 adjudicator calls per review, at most 3 reviews a
position a day.

## Later

- **Research (Phase 3).** Turn on `RESEARCH_MODE=shadow` only after SEC evidence has been
  collecting for a few days, and set a planner model and, on Vertex, `RESEARCH_DAILY_COST_BUDGET`
  and the token rates.

  Research currently runs inside the worker's trading cycle and can delay decisions by minutes
  (see Known issues in the [guide](RESEARCH_GUIDE.md)).
- **Challenger (Phase 7).** Set `RESEARCH_CHALLENGER_MODE=shadow` and a challenger model.

  It costs up to about 100 extra model calls and about 240 MB of storage a week.

## Undo

**To switch features off:** set the modes back (`disabled`/`off`/`false`), then run
`docker compose up -d`. Recorded research data stays and is harmless.

**To restore the backup:**

```bash
docker compose stop worker news_collector option_manager marketdata_eod_sync api
docker compose run --rm --no-deps worker sh -c 'rm -f "$DB_PATH"-wal "$DB_PATH"-shm && gunzip -c /var/lib/stock-experiment/backup-before-research.db.gz > "$DB_PATH"'
git checkout <previous commit> && docker compose build && docker compose up -d
```
