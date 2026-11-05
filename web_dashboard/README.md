# Web Dashboard

This folder contains the React + TypeScript dashboard for the stock-trading experiment.

## Purpose

The dashboard is a monitoring UI for the backend worker and API. It is not the trading engine itself.

It is organised as four pages, each answering one question. Views within a
page are tabs, and every page is reachable at `#/<page>` or `#/<page>/<view>`.
[src/routes.tsx](src/routes.tsx) is the source of truth.

| Page | Question | Views |
| --- | --- | --- |
| `#/live` | What is it doing right now? | Overview: account KPIs, latest decision, equity / drawdown / daily P/L, newsfeed, decisions, orders, news-lag scatter, holdings, data pipeline, V2 safeguards |
| `#/decisions` | Why did it do that? | Decisions, News, Exposure map |
| `#/research` | Is the strategy worth keeping? | Expectancy, News lag, Backtests, Followers, V2 Rollout |
| `#/system` | Is the machinery healthy? | Health, Risk, Extraction |

Live carries a V2 Safeguards strip: asking for `enforce` does not make a gate
enforce, so the counts there say how many controls are actually in force and
name any that were silently downgraded. The proofs behind them are one click
away on `#/research/rollout`.

The Decisions view carries the other half of that picture: for each decision it
lists the V2 gates that recorded a verdict against that candidate, and whether
the verdict was shadow (recorded, but the trade went ahead) or enforced. Where
the candidate lifecycle tables are absent it says so, because an unreadable
ledger and a candidate nothing objected to are not the same thing.

Demo Mode seeds a static, read-only walkthrough; the Decisions and Exposure Map
views render it.

The paths from when each view was its own page (`#/exposure-map`,
`#/v2-rollout`, `#/dashboard` and the rest) still resolve to their new home, so
existing links and bookmarks keep working.

## Main Files

- [src/App.tsx](src/App.tsx): the shell — top bar, status line, view tabs, active page
- [src/statusBar.tsx](src/statusBar.tsx) / [src/shellStatus.ts](src/shellStatus.ts): the status line under the top bar
- [src/ui/](src/ui/): shared Tailwind components (Panel, StatStrip, DataTable, Tag, ...)
- [src/routes.tsx](src/routes.tsx): the page table, hash routing and demo-mode rules
- [src/dashboardHome.tsx](src/dashboardHome.tsx): Live › Overview, which lays out the panels below
- [src/dashboardKpis.tsx](src/dashboardKpis.tsx) / [src/dashboardKpisApi.ts](src/dashboardKpisApi.ts): the account KPIs, including unrealized P/L
- [src/whyBotTraded.tsx](src/whyBotTraded.tsx): the latest-decision panel (company, strategist, contract, fill and exit plan)
- [src/liveNewsfeed.tsx](src/liveNewsfeed.tsx), [src/liveDecisions.tsx](src/liveDecisions.tsx), [src/liveNewsLag.tsx](src/liveNewsLag.tsx), [src/openPositionsTable.tsx](src/openPositionsTable.tsx), [src/livePipeline.tsx](src/livePipeline.tsx): the other Live panels
- [src/liveStats.ts](src/liveStats.ts): the Live page's runtime-stats strip
- [src/systemHealth.tsx](src/systemHealth.tsx) / [src/systemRisk.tsx](src/systemRisk.tsx) / [src/newsExtraction.tsx](src/newsExtraction.tsx): System › Health, Risk and Extraction
- [src/Graph.tsx](src/Graph.tsx): the Live equity row — equity with trade markers, drawdown, daily P/L
- [src/equitySeries.ts](src/equitySeries.ts): portfolio-history fetch and the series the charts plot
- [src/orderCard.tsx](src/orderCard.tsx): the Live orders table ([src/occ.ts](src/occ.ts) formats option symbols)
- [src/exposureMap.tsx](src/exposureMap.tsx): grounded article-to-position graph
- [src/decisionsPage.tsx](src/decisionsPage.tsx) and [src/decisions.ts](src/decisions.ts): every decision with its frozen evidence, decision path and gates (the old Ledger and Autopsy, merged)
- [src/v2Status.tsx](src/v2Status.tsx): the V2 Rollout view — mode counts, would-block rate per control, promotion evidence; Live shows a compact version ([src/liveSafeguards.tsx](src/liveSafeguards.tsx))
- [src/v2StatusApi.ts](src/v2StatusApi.ts): `/api/v2-status` types, fetch and the would-block rows, shared by both
- [src/decisionExpectancy.tsx](src/decisionExpectancy.tsx) / [src/expectancy.ts](src/expectancy.ts), [src/newsLag.tsx](src/newsLag.tsx), [src/backtest.tsx](src/backtest.tsx) / [src/backtests.ts](src/backtests.ts), [src/followerAccounts.tsx](src/followerAccounts.tsx): the other Research views
- [nginx.conf](nginx.conf): nginx config used in the Docker image
- [Dockerfile](Dockerfile): frontend build + nginx image

## API Usage

The frontend reads these routes:

- status and account: `/api/script-status`, `/api/dashboard-kpis`, `/api/portfolio-history`, `/api/open-positions`, `/api/executed-trades`, `/api/why-bot-traded`, `/api/risk-controls`
- decisions and news: `/api/decision-autopsies`, `/api/company-decisions`, `/api/opportunist-company-news`, `/api/news-extraction`, `/api/exposure-map/articles`, `/api/exposure-map/<article_id>`, `/api/exposure-map/runs`
- research: `/api/decision-expectancy`, `/api/news-lag`, `/api/backtests`, `/api/follower-accounts`, `/api/forward-evaluation`, `/api/v2-status`
- `/api/demo-mode`

The repo-level [README](../README.md#api-endpoints) lists every API route.

In Docker, nginx proxies `/api/*` to the internal Flask service.

## Local Development

Install dependencies:

```bash
npm install
```

Run the Vite dev server:

```bash
npm run dev
```

Run the tests (Vitest + Testing Library):

```bash
npm test
```

By default, the app is available at:

```text
http://localhost:5173
```

The demo walkthrough is available at:

```text
http://localhost:5173/#/decisions/map?demo=1
```

## Docker

The production Docker image:

1. builds the Vite app
2. copies the static bundle into nginx
3. proxies API requests to the backend container

The repo-level `docker-compose.yml` publishes ports 80 and 443. With the
`docker-compose.local.yml` overlay, the dashboard is on:

```text
http://localhost:8080
```

With `docker-compose.tunnel.yml`, no ports are published and Cloudflare serves it.

## Styling

Tailwind CSS v4 + daisyUI 5, themed in [src/index.css](src/index.css), which
is the only stylesheet. The per-component `.css` files were removed in the
redesign; see [docs/redesign/REDESIGN.md](docs/redesign/REDESIGN.md) for what it
changed and what is still open.

## Notes

- The status line polls the API so it can show each component's state, and flags the worker when its heartbeat is more than 90 seconds old.
