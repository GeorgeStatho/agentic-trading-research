# Deployment and operations

How to configure, run and operate the stack. The [README](../README.md) has the short version;
[DEVELOPMENT.md](DEVELOPMENT.md) covers installing for development and running the tests.

## Services

The current runtime stack is:

```text
React dashboard (web_dashboard)
        |
        v
Flask API (api.py)
        |
        +--> Alpaca portfolio history
        +--> SQLite-backed executed trade journal
        +--> shared runtime JSON
        |
        v
Python worker (Python Scripts/main.py)
        |
        +--> strategist stage
        +--> manager stage
        +--> deterministic option selection
        +--> Friday entry guard for new option buys
        +--> Alpaca paper-trade execution
        |
        v
Dedicated option manager (Python Scripts/option_manager_main.py)
        |
        +--> DTE-aware option position management
        +--> deterministic exits / auto-close rules
        |
        v
Persisted market/news DB state
        ^
        |
News collector (Python Scripts/news_collector_main.py)
        |
        +--> scraping + market/news collection
        +--> sector/industry/company classification
```

Docker Compose currently runs:

- `web`: nginx + built React frontend
- `api`: Flask API
- `worker`: long-running strategist/manager/trading loop using existing DB data
- `option_manager`: long-running existing-position management loop for options
- `news_collector`: long-running scrape/classification refresh loop
- `marketdata_eod_sync`: daily historical option-quote cache refresh (see below)
- `certbot`: Let's Encrypt certificate renewal
- `ollama`: optional profile only, for local fallback

Optional overlays add services on top of `docker-compose.yml` (pass each with another `-f`):

- [docker-compose.observability.yml](../docker-compose.observability.yml): Dozzle (live container logs, `127.0.0.1:8081`) and Uptime Kuma (uptime alerts, `127.0.0.1:3001`). Both bind to localhost only, so reach them through an SSH tunnel. Point an Uptime Kuma HTTP monitor at `http://api:5000/api/health/components`, which returns 503 when a loop is down, stalled or failed in the last hour.

## Model providers

The repo supports model backends behind a shared helper:

- `vertex`: recommended for cloud deployment
- `ollama`: optional local fallback
- `openai`: OpenAI Responses API, including the configured Exposure Map model path

The provider abstraction lives in [Python Scripts/agentCallers/agent_helpers/llm_client.py](../Python%20Scripts/agentCallers/agent_helpers/llm_client.py), with each provider family in its own `llm_vertex.py` / `llm_openai.py` beside it.

The company opportunist classifies several articles per request, because free tiers cap requests per day long before they cap tokens. Each article body is cut to `COMPANY_OPPORTUNIST_ARTICLE_BODY_CHAR_LIMIT` (default 1200 characters), and `CLASSIFIER_BATCH_TOKENS` sets the token budget per request.

The stage-by-stage model settings live in [.env.example](../.env.example).

## Configuration and first run

### 1. Create environment config

Use the template:

```bash
cp .env.example .env
```

Then fill in at minimum:

- `PUBLIC_KEY`
- `PRIVATE_KEY`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`

For historical option-quote fetching, set `MARKETDATA_TOKEN`. The optional
`MARKETDATA_DAILY_CREDIT_BUDGET` defaults to `100`; set it to `0` to disable
outbound EOD requests. This is a conservative local allowance: every attempt
uses at least one unit, including failures and free responses, and larger
provider-reported credit costs are counted. Cached windows use no allowance.
The daily window resets at 09:30 America/New_York, including daylight saving
changes. See [MarketData rate limits](https://www.marketdata.app/docs/api/rate-limiting/)
and [credit resets](https://www.marketdata.app/docs/api/troubleshooting/running-out-of-credits/).

Allowance and provider cooldowns persist in SQLite and are shared by the
Research dashboard and EOD CLI when they use the same database. `force` only
bypasses the quote cache, never the budget. HTTP 429 or a response reporting
zero remaining credits stops the batch; subsequent calls stay paused until
`Retry-After` / the provider reset permits retrying. Without usable headers,
the next local daily reset is used. The dashboard shows the pause reason,
retry time, and allowance used. Quotes already imported remain usable, and
unattempted windows remain queued. Manual fetching resumes on your next request
after the pause. The optional scheduler below runs separately.

For intraday backtest marks, the Backtests page's **Fetch next 1-min bar batch**
(`POST /api/backtests/alpaca-minute-bars`) stores Alpaca 1-minute stock bars (SIP)
and option trade bars for stored decisions' hold windows, using `PUBLIC_KEY` /
`PRIVATE_KEY` for market data only. Each session is fetched once, including
sessions with no option trades. The backtest then marks modelled trades every
minute (`model_black_scholes_1m`), takes a real option trade as the price in any
minute that has one, and records each trade's share of marks from real trades.
The `marketdata_eod_sync` service also runs this fetch on its own, 100 decisions
at most once an hour, and never starts one while Alpaca's market clock says the
market is open or opens within 30 minutes, so production trading keeps Alpaca's
rate limit (holidays and early closes follow the clock). Sessions are
fetched once, so after the backlog clears most runs make no requests. Set
`ALPACA_MINUTE_BARS_AUTO_FETCH=false` to turn it off.

The local counter starts when this protection is installed; it cannot reconstruct
prior usage or track requests made by other applications/databases. Provider
headers still trigger a pause if that outside usage exhausts the account.

The `marketdata_eod_sync` Docker service automatically refreshes only the quote
cache once daily at **10:00 America/New_York**. It starts with the normal Compose
stack and shares the API's database and daily allowance. Set
`MARKETDATA_AUTO_FETCH_ENABLED=false` to disable it. Start or update it separately:

```bash
docker compose up -d --build marketdata_eod_sync
docker compose logs --tail=100 marketdata_eod_sync
```

The default batch size is 100. Each run fetches decisions whose complete entry
and holding windows ended at least 24 hours ago (about nine-day-old decisions
with the default 1,440-minute entry allowance and 168-hour hold). Match
`MARKETDATA_AUTO_FETCH_MAX_HOLD_HOURS` to the holding horizon you want cached.
Provider publication delays or holidays can still leave gaps; unresolved windows
are retried no sooner than seven days later, controlled by
`MARKETDATA_AUTO_FETCH_RETRY_DAYS`. Unattempted windows keep priority.

The daily claim is saved in `marketdata_eod_sync_runs`, preventing duplicate
batches across restarts or simultaneous schedulers sharing the database. Starting
after 10:00 runs today's batch immediately if it has not already been claimed.
A failed/interrupted or rate-limited daily batch resumes through the normal queue
on a subsequent day's run, subject to the provider cooldown and shared budget.
No catch-up loop spends multiple days' allowances at startup. The service creates
no backtests and places no trades; run a new backtest when you want updated results.

Without Docker, keep this process running under your existing process supervisor:

```bash
python "Python Scripts/marketdata_eod_sync_main.py"
```

Add `--once` to check the schedule once and exit; it still respects the daily claim
and 10:00 schedule. The service logs fetch summaries and pause reasons. Missing
`MARKETDATA_TOKEN` causes it to wait without making provider requests.

### 2. Run with Docker

Default cloud-friendly startup:

```bash
docker compose up --build
```

If you want the local Ollama fallback too:

```bash
docker compose --profile ollama up --build
```

### 3. First-time HTTPS setup for a public server

If you are deploying the dashboard publicly at `dashboard.example.com`, do the one-time certificate bootstrap first.

Prerequisites:

- create an A record for `dashboard.example.com` pointing at your server IP
- make sure ports `80` and `443` are open on the server
- make sure Docker and Docker Compose are available on the host

The repo already includes:

- [docker-compose.bootstrap.yml](../docker-compose.bootstrap.yml): temporary bootstrap override that mounts the HTTP-only ACME Nginx config
- [web_dashboard/nginx.bootstrap.conf](../web_dashboard/nginx.bootstrap.conf): HTTP-only config used before the certificate exists
- [web_dashboard/nginx.conf](../web_dashboard/nginx.conf): final HTTPS config for `dashboard.example.com`
- [scripts/setup-https.sh](../scripts/setup-https.sh): helper script that runs the bootstrap flow

Run:

```bash
./scripts/setup-https.sh dashboard.example.com your-email@example.com
```

Or without email:

```bash
./scripts/setup-https.sh dashboard.example.com
```

What the script does:

1. creates `certbot/conf` and `certbot/www`
2. starts the temporary web container with the bootstrap Nginx config
3. requests the Let's Encrypt certificate for `dashboard.example.com`
4. restarts the web container with the production HTTPS Nginx config
5. starts the full stack, including automatic certificate renewal via the `certbot` service


### 4. Open the dashboard

Local Docker default:

```text
http://localhost
```

Public HTTPS deployment target:

```text
https://dashboard.example.com/#/dashboard
```

### 5. Common development commands

```bash
python3 scripts/project.py test tests.test_exposure_map tests.test_settings
python3 scripts/project.py dashboard build
python3 scripts/project.py demo-url
```

## Vertex AI authentication

The current Docker configuration is set up for Google Application Default Credentials rather than a checked-in key file.

Recommended cloud setup:

- attach a service account to the VM / Cloud Run workload
- grant it `roles/aiplatform.user`
- set:

```env
LLM_PROVIDER=vertex
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
```

On Google Cloud, the containers should authenticate automatically through the attached service account.

To run the high-volume news classifiers on the Gemini API free tier instead, while the strategist and
manager stay on Vertex, set an AI Studio key and override those stages' provider:

```env
GEMINI_API_KEY=your-ai-studio-key
WORLD_NEWS_PROVIDER=gemini
SECTOR_OPPURUNTIST_PROVIDER=gemini
INDUSTRY_OPPURUNTINST_PROVIDER=gemini
COMPANY_OPPURUNTIST_PROVIDER=gemini
```

Free-tier quotas are unpublished and can change; a call that hits the daily quota fails at once
instead of retrying, and the free tier may use prompts for training.

When the quota runs out, the classifiers continue down an ordered list of fallbacks. Free Gemini
quotas are counted per model, so another Flash-Lite version is a fresh allowance; after that, any
OpenAI-compatible Chat Completions endpoint works (Mistral's free plan shown here):

```env
OPENAI_COMPATIBLE_BASE_URL=https://api.mistral.ai/v1
OPENAI_COMPATIBLE_API_KEY=your-mistral-key
CLASSIFIER_FALLBACKS=gemini:gemini-3.5-flash-lite,openai_compatible:mistral-small-latest
```

Each `*_article_processing` row stores the model that actually classified the article in `model`,
and `raw_json.answered_by` adds the provider, the version the provider reported, and whether the
fallback answered.

Local Docker note:

- the containers mount the host ADC directory from `~/.config/gcloud`
- refresh local ADC with:

```bash
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform
```

- if you still receive `ACCESS_TOKEN_SCOPE_INSUFFICIENT`, verify the runtime is using ADC rather than a different cached credential source

## API endpoints

The Flask service in [api.py](../api.py) registers its routes from [api_routes/](../api_routes):

- Health: `GET /api/health`, `GET /api/health/components` (503 when a loop is down, stalled or recently failed), `GET /api/script-status`
- Account and trades: `GET /api/portfolio-history`, `GET /api/dashboard-kpis`, `GET /api/open-positions`, `GET /api/executed-trades`, `GET /api/trade-execution-output`, `GET /api/why-bot-traded`, `GET /api/risk-controls`, `GET /api/market-regime`
- Decisions and news: `GET /api/company-decisions`, `GET /api/decision-autopsies`, `GET /api/opportunist-company-news`, `GET /api/news-extraction`, `GET /api/exposure-map/articles`, `GET /api/exposure-map/<article_id>`, `POST /api/exposure-map/runs`
- Research: `GET /api/decision-expectancy`, `GET /api/news-lag`, `GET /api/follower-accounts`, `GET /api/forward-evaluation`, `GET /api/thesis-validator`, `GET /api/v2-status`, `GET /api/backtests`, `GET /api/backtests/<run_id>`, `POST /api/backtests`, `POST /api/backtests/marketdata-eod`, `POST /api/backtests/alpaca-minute-bars`
- Other: `GET /api/demo-mode`, `POST /api/telemetry/startup`, `GET /api/telemetry/summary`

These are intended for the dashboard and internal monitoring. `GET /api/telemetry/summary` returns 401 unless `TELEMETRY_RECEIVER_READ_AUTH_TOKEN` (or `TELEMETRY_RECEIVER_AUTH_TOKEN`) is set and sent as a bearer token.

## Runtime outputs

The runtime currently persists trade data in two ways:

- SQLite table `option_trade_executions` for durable executed-trade history used by `/api/executed-trades`
- shared JSON outputs for compatibility and dashboard/runtime state

The shared JSON outputs currently written are:

- `script_status.json`
- `option_manager_status.json`
- `news_collector_status.json`, `marketdata_eod_sync_status.json` and `db_backup_status.json` (loop heartbeats read by `/api/health/components`)
- `trade_execution_output.json`
- `selected_options_output.json`
- `option_position_management_output.json`

During Docker runs, the main shared runtime paths are:

- `/shared/script_status.json`
- `/shared/option_manager_status.json`
- `/shared/trade_execution_output.json`
- `/shared/option_position_management_output.json`

## Startup telemetry

The project now includes an optional, privacy-respecting startup ping system for counting unique installs that actually ran.

Privacy details for the current telemetry behavior live in [PRIVACY.md](../PRIVACY.md).

- telemetry is enabled by default
- when enabled, each install generates a random local `install_id` and stores it in `TELEMETRY_INSTALLATION_ID_PATH`
- on startup, the `worker`, `option_manager`, `news_collector`, and `api` processes send a small JSON POST to `TELEMETRY_ENDPOINT`
- this repo now includes a tiny receiver endpoint at `POST /api/telemetry/startup` and a summary endpoint at `GET /api/telemetry/summary`
- the receiver stores one SQLite row per unique `install_id` and increments `startup_ping_count` on repeat startups
- startup pings can be left public, while the summary endpoint can be protected with a separate read token
- the payload only includes:
  - `event`
  - `telemetry_version`
  - `project`
  - `runtime`
  - `install_id`
  - `install_created_at`
  - `started_at`
  - optional `release`

Suggested env vars:

- `TELEMETRY_ENABLED=true`
- `TELEMETRY_ENDPOINT=https://your-telemetry-endpoint.example.com/api/telemetry/startup`
- `TELEMETRY_AUTH_TOKEN=...`
- `TELEMETRY_INSTALLATION_ID_PATH=/shared/telemetry_installation.json`
- `TELEMETRY_TIMEOUT_SECONDS=2.0`
- `TELEMETRY_RELEASE=2026-05-24`
- `TELEMETRY_RECEIVER_WRITE_AUTH_TOKEN=...`
- `TELEMETRY_RECEIVER_READ_AUTH_TOKEN=...`
- `TELEMETRY_RECEIVER_AUTH_TOKEN=...`

Recommended public-clone setup:

- leave `TELEMETRY_RECEIVER_WRITE_AUTH_TOKEN` unset so public startup pings can be recorded
- set `TELEMETRY_RECEIVER_READ_AUTH_TOKEN` on your VM so `GET /api/telemetry/summary` stays private
- set `TELEMETRY_ENDPOINT` in the shared `.env.example` to your VM's `POST /api/telemetry/startup` URL
- leave `TELEMETRY_AUTH_TOKEN` unset in public clones unless you explicitly choose to protect write access too

Count unique installs by unique `install_id` values on your telemetry receiver. Multiple services from the same deployment may ping separately, so dedupe by `install_id` when estimating how many people actually ran the project.
