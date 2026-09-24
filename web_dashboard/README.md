# Web Dashboard

This folder contains the React + TypeScript dashboard for the stock-trading experiment.

## Purpose

The dashboard is a monitoring UI for the backend worker and API. It is not the trading engine itself.

It is organised as three pages, each answering one question. Views within a
page are tabs, and every page is reachable at `#/<page>` or `#/<page>/<view>`.

| Page | Question | Views |
| --- | --- | --- |
| `#/live` | What is it doing right now? | worker status, account KPIs, risk controls, V2 safeguards, equity curve, open positions, executed orders |
| `#/decisions` | Why did it do that? | Ledger, Autopsy, Company News, Exposure Map |
| `#/research` | Is the strategy worth keeping? | Expectancy, Backtests, V2 Rollout |

Live carries a V2 Safeguards strip: asking for `enforce` does not make a gate
enforce, so the counts there say how many controls are actually in force and
name any that were silently downgraded. The proofs behind them are one click
away on `#/research/rollout`.

Demo Mode seeds a static, read-only walkthrough; the Autopsy and Exposure Map
views render it.

The paths from when each view was its own page (`#/exposure-map`,
`#/v2-rollout`, `#/dashboard` and the rest) still resolve to their new home, so
existing links and bookmarks keep working.

## Main Files

- [src/App.tsx](src/App.tsx): the shell — top nav, per-page sidebar, active page
- [src/routes.tsx](src/routes.tsx): the page table, hash routing and demo-mode rules
- [src/dashboardHome.tsx](src/dashboardHome.tsx): the Overview page
- [src/dashboardSections.ts](src/dashboardSections.ts): Overview's sidebar anchors
- [src/scriptStatus.tsx](src/scriptStatus.tsx): status indicator
- [src/Graph.tsx](src/Graph.tsx): portfolio-history chart
- [src/orderCard.tsx](src/orderCard.tsx): trade execution cards
- [src/exposureMap.tsx](src/exposureMap.tsx): grounded article-to-position graph
- [src/decisionAutopsy.tsx](src/decisionAutopsy.tsx): immutable decision review
- [src/v2Status.tsx](src/v2Status.tsx): the V2 Rollout view — control modes, shadow verdicts, promotion evidence
- [src/v2Safeguards.tsx](src/v2Safeguards.tsx): the mode counts and warnings, shown on Live and on the rollout view
- [src/v2StatusApi.ts](src/v2StatusApi.ts): `/api/v2-status` types and fetch, shared by both
- [src/displayPreviousorder.ts](src/displayPreviousorder.ts): portfolio-history fetch + transform helper
- [nginx.conf](nginx.conf): nginx config used in the Docker image
- [Dockerfile](Dockerfile): frontend build + nginx image

## API Usage

The frontend expects these routes:

- `/api/health`
- `/api/script-status`
- `/api/portfolio-history`
- `/api/trade-execution-output`
- `/api/exposure-map/articles`
- `/api/exposure-map/<article_id>`
- `/api/decision-autopsies`
- `/api/demo-mode`
- `/api/v2-status`

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

When run through the repo-level `docker-compose.yml`, the dashboard is exposed on:

```text
http://localhost:8080
```

## Styling

The current UI is split across:

- [src/index.css](src/index.css)
- [src/App.css](src/App.css)
- [src/graph.css](src/graph.css)
- [src/card.css](src/card.css)
- [src/status.css](src/status.css)

## Notes

- The graph currently fetches portfolio-history data on mount, not on a polling interval.
- The status badge does poll the API so it can show `running`, `paused`, `error`, or `down`.
