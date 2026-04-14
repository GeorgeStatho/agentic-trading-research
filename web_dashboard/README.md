# Web Dashboard

This folder contains the React + TypeScript dashboard for the stock-trading experiment.

## Purpose

The dashboard is a monitoring UI for the backend worker and API. It is not the trading engine itself.

It currently shows:

- script/worker status
- portfolio-history graph
- trade execution output cards

## Main Files

- [src/App.tsx](src/App.tsx): top-level layout
- [src/scriptStatus.tsx](src/scriptStatus.tsx): status indicator
- [src/Graph.tsx](src/Graph.tsx): portfolio-history chart
- [src/orderCard.tsx](src/orderCard.tsx): trade execution cards
- [src/displayPreviousorder.ts](src/displayPreviousorder.ts): portfolio-history fetch + transform helper
- [nginx.conf](nginx.conf): nginx config used in the Docker image
- [Dockerfile](Dockerfile): frontend build + nginx image

## API Usage

The frontend expects these routes:

- `/api/health`
- `/api/script-status`
- `/api/portfolio-history`
- `/api/trade-execution-output`

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
