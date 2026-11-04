# Dashboard redesign

Spec for restyling `web_dashboard/` into a dense, chart-heavy research terminal:
QuantConnect's runtime-stats banner and stacked charts, Benzinga Pro's compact
data tables, on a charcoal + orange palette. Mockups live in
[`mockups/`](mockups/README.md); this file maps them onto the real code.

Styling moves from hand-written per-component `.css` files to **Tailwind CSS
v4 + daisyUI 5**: Tailwind utilities for layout, panels, tables and charts;
daisyUI components for controls (buttons, inputs, selects, tabs, badges).

Scope is **presentation first**. Every page keeps its current endpoint and
data flow unless a section below lists a new field under "Needs".

---

## 1. Ground rules

These apply to every page and are the main thing a reviewer should check.

1. **One accent.** Orange (`primary`) marks the active nav item, links, the
   primary button, the equity line and "requested enforce". Nothing else.
2. **Red and green mean money.** `gain` / `loss` (and daisyUI's `success` /
   `error`, which share their values) are for P/L, pass/block and errors only.
   "Market closed" and "high volatility" are neutral, not red.
3. **Sentence-case labels.** Drop the uppercase/monospace eyebrow labels
   (`ACCOUNT EQUITY`, `WHY DID THE BOT TRADE?`). Labels are
   `text-[11px] text-dim`.
4. **Numbers line up.** `tabular-nums` is on `body` (see § 2); tickers,
   contracts, timestamps and run ids use `font-mono`.
5. **Density.** 12–13px body, `rounded-md` (6px) panels, `gap-3` (12px)
   between panels, panels with a thin header bar. No card-inside-card.
6. **No banner walls.** The full-width yellow notice boxes become one line of
   `text-xs text-warning` under the stats strip. Keep the text; lose the box.
   Don't use daisyUI `alert` for these.
7. **Show sample size.** Any rate or average computed from fewer than the
   page's guardrail (5 closed trades) shows `n=` and is muted. Sharpe/Sortino
   are hidden until n ≥ 20.
8. **One page width.** Every page uses the same container:
   `mx-auto max-w-[1400px] px-5`. Today Live, Decisions and Research each use
   different widths.
9. **Phone width works.** Rows are `flex flex-wrap` with `basis-*` minimums,
   wide tables scroll inside `overflow-x-auto`, nothing uses `100vh` sidebars.
10. **Tailwind first, daisyUI for controls.** Use daisyUI for `btn`, `input`,
    `select`, `tabs`, `badge`, `join`, `progress`, `collapse`, `loading`. Build
    panels, stat strips, tables and chart layouts from plain utilities, because
    daisyUI's `card` / `stats` defaults are roomier than this design.

---

## 2. Setup and tokens

### Install

```bash
cd web_dashboard
npm install -D tailwindcss @tailwindcss/vite daisyui
```

`vite.config.ts`: add the plugin next to React:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:5000',
    },
  },
})
```

### `src/index.css` (replaces the current file)

Three layers: the daisyUI theme (colors daisyUI components read), Tailwind
`@theme` tokens for the colors daisyUI doesn't have, and a temporary block that
keeps the old variable names alive until every component is converted.

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
@import "tailwindcss";

/* Only our theme; no built-in daisyUI themes in the bundle. */
@plugin "daisyui" {
  themes: false;
}

@plugin "daisyui/theme" {
  name: "terminal";
  default: true;
  prefersdark: true;
  color-scheme: dark;

  --color-base-100: #121214;      /* page */
  --color-base-200: #1a1a1d;      /* panels */
  --color-base-300: #2c2c31;      /* borders */
  --color-base-content: #e8e8ea;

  --color-primary: #f98309;       /* QuantConnect orange */
  --color-primary-content: #121214;
  --color-secondary: #58a6ff;
  --color-secondary-content: #0b1220;
  --color-accent: #f98309;
  --color-accent-content: #121214;
  --color-neutral: #232327;
  --color-neutral-content: #c2c2c8;

  --color-info: #58a6ff;
  --color-info-content: #0b1220;
  --color-success: #3fb950;
  --color-success-content: #0b1a10;
  --color-warning: #ffb066;
  --color-warning-content: #1f1406;
  --color-error: #f85149;
  --color-error-content: #1f0a09;

  --radius-selector: 0.25rem;     /* badges, toggles */
  --radius-field: 0.25rem;        /* buttons, inputs, selects */
  --radius-box: 0.375rem;         /* panels, dropdowns */
  --size-selector: 0.25rem;
  --size-field: 0.25rem;          /* smaller controls than daisyUI's default */
  --border: 1px;
  --depth: 0;                     /* flat: no gradients or shadows */
  --noise: 0;
}

/* Colors daisyUI has no slot for. Each becomes a utility:
   bg-head, bg-raised, text-dim, text-ink-2, text-gain, bg-loss-bg, ... */
@theme {
  --color-head: #161618;          /* top bar, sub-bars, table header */
  --color-raised: #232327;        /* row dividers, chips, selected row */
  --color-grid: #26262b;          /* chart grid lines */
  --color-ink-2: #c2c2c8;         /* secondary text */
  --color-dim: #9a9aa2;           /* labels, 6:1 on panels */
  --color-gain: #3fb950;
  --color-gain-text: #56d364;
  --color-gain-bg: #12261a;
  --color-loss: #f85149;
  --color-loss-text: #ff9b94;
  --color-loss-bg: #2d1617;
  --color-accent-bg: #24180b;     /* ticker chips, active filter */
  --color-accent-line: #5a3510;
  --color-ticker: #ffb066;        /* ticker text on accent-bg */
  --font-sans: "Inter", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
}

@layer base {
  body {
    @apply min-w-80 bg-base-100 font-sans text-[13px] leading-snug text-base-content tabular-nums antialiased;
  }
  a { @apply text-primary hover:text-[#ffa347]; }
  #root { min-height: 100svh; }
  button, a { -webkit-tap-highlight-color: transparent; }
  /* Preflight sets `cursor: default` on buttons. */
  button:not(:disabled), [role="button"]:not(:disabled) { cursor: pointer; }

  /* TEMPORARY: browser defaults (heading size/weight, p/dl/list margins,
     bullets, `small`) restored inside the roots of unconverted components.
     See "Things that shift" below. */
  :where(.autopsy-page, .backtest-page, /* ...every legacy root */) { ... }
}

/* TEMPORARY: old names, re-pointed, so unconverted component CSS keeps
   working. Delete this block when `grep -rn "var(--" src/*.css` is empty.
   There is no legacy `--border`: daisyUI uses that name for border width,
   so old CSS reads `--outline-variant` for the border color instead. */
:root {
  --background: var(--color-base-100);
  --surface: var(--color-base-100);
  --surface-container-low: var(--color-head);
  --surface-container: var(--color-base-200);
  --surface-container-high: var(--color-raised);
  --surface-container-highest: var(--color-base-300);
  --outline-variant: var(--color-base-300);
  --on-surface: var(--color-base-content);
  --on-surface-variant: var(--color-ink-2);
  --primary: var(--color-primary);
  --primary-container: var(--color-accent-bg);
  --secondary: var(--color-gain);
  --secondary-container: var(--color-gain-bg);
  --tertiary: var(--color-loss-text);
  --tertiary-container: var(--color-loss);
  --error-container: var(--color-loss-bg);
  --on-error-container: var(--color-loss-text);
  --text: var(--color-ink-2);
  --text-dim: var(--color-dim);
  --text-h: var(--color-base-content);
  --bg: var(--color-base-100);
  --accent: var(--color-primary);
  --accent-bg: var(--color-accent-bg);
  --accent-border: var(--color-accent-line);
  --shadow: none;
  --sans: var(--font-sans);
  --heading: var(--font-sans);
  --mono: var(--font-mono);
}
```

Check the daisyUI option names (`themes: false`, the `--size-*` / `--depth`
keys) against the installed daisyUI version's theme docs; the
[theme generator](https://daisyui.com/theme-generator/) prints a valid block.

### Things that shift when Tailwind lands

PR 1 (setup) handled these so unconverted pages look as before apart from
palette, font and the 13px body:

- **`--border`.** daisyUI themes define `--border: 1px` (border width for
  `btn`, `input`, ...). The old CSS used `var(--border)` as a color, so every
  such use was renamed to `var(--outline-variant)` (same `base-300` value).
- **`rem`.** The old `:root { font: 14px/... }` made `1rem` = 14px; Tailwind
  leaves the root at 16px. The old `.css` files had their `rem` sizes
  rewritten as px at 14px (`.75rem` → `10.5px`). New code uses Tailwind's
  scale as usual.
- **Preflight** removes heading sizes/weight, `p`/`dl`/list margins, bullets
  and `small`'s size. A temporary block in `index.css` restores those browser
  defaults inside the root class of each unconverted component. When you
  convert a component, drop its root from that list, and give converted
  markup explicit `text-*`, `font-*`, `list-disc pl-5` and margins.

### Chart colors: `src/ui/chartTheme.ts`

Recharts writes colors as SVG attributes, where `var(--…)` is not reliably
resolved, so charts take hex values from one module that mirrors the theme:

```ts
// Keep in sync with src/index.css.
export const chartTheme = {
  accent: '#f98309', gain: '#3fb950', loss: '#f85149', info: '#58a6ff',
  ink: '#e8e8ea', dim: '#9a9aa2', grid: '#26262b', axis: '#3a3a40',
  panel: '#1a1a1d', neutral: '#6e6e76',
} as const
```

### Conversion rule

When a component is restyled, its classes move into the JSX and its `.css`
file is deleted in the same PR (e.g. `whyBotTraded.css` goes when
`whyBotTraded.tsx` is converted). Don't mix: a converted component imports no
`.css`. Optional: add `prettier-plugin-tailwindcss` to keep class order
consistent.

Alternative palette "Deep Teal" (not applied) is in `mockups/Palette.dc.html`:
swap the theme values for ground `#061012`, panel `#0c1a1d`, primary
`#2dd4bf`, success `#a3e635`, error `#fb7185`.

---

## 3. Shared components (new: `src/ui/`)

Tailwind classes only, no `.css` files. These replace the card markup that's
repeated across pages. Class strings below are the starting point; match the
mockups for anything not listed.

### `Panel.tsx`

```tsx
type PanelProps = { title: ReactNode; meta?: ReactNode; actions?: ReactNode; flush?: boolean; className?: string; children: ReactNode }

export function Panel({ title, meta, actions, flush, className = '', children }: PanelProps) {
  return (
    <section className={`min-w-0 rounded-box border border-base-300 bg-base-200 ${className}`}>
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-base-300 px-3.5 py-2">
        <h2 className="text-xs font-semibold">
          {title}{meta ? <span className="font-normal text-dim"> · {meta}</span> : null}
        </h2>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </header>
      <div className={flush ? '' : 'p-3.5'}>{children}</div>
    </section>
  )
}
```

`flush` is for panels whose body is a table, so rows run edge to edge.

### `StatStrip.tsx`

The QuantConnect runtime-stats row. Replaces the KPI cards in
`dashboardKpis.tsx` and the four-card summary rows on every Research page.

```tsx
type Stat = { label: string; value: ReactNode; sub?: ReactNode; tone?: 'gain' | 'loss' | 'muted' }
const toneClass = { gain: 'text-gain', loss: 'text-loss', muted: 'text-dim' }

export function StatStrip({ items }: { items: Stat[] }) {
  return (
    <section className="flex flex-wrap rounded-box border border-base-300 bg-base-200">
      {items.map((s) => (
        <div key={s.label} className="min-w-[120px] flex-1 border-r border-raised px-4 py-3 last:border-r-0">
          <div className="text-[11px] text-dim">{s.label}</div>
          <div className={`mt-0.5 text-[15px] font-semibold ${s.tone ? toneClass[s.tone] : ''}`}>{s.value}</div>
          {s.sub ? <div className="text-[11px] text-dim">{s.sub}</div> : null}
        </div>
      ))}
    </section>
  )
}
```

### The rest

| Component | Build it with | Replaces / used by |
| --- | --- | --- |
| `DataTable` | daisyUI `table table-xs` inside `overflow-x-auto`; header row `bg-head text-[11px] font-medium text-dim`; rows `border-raised`; numeric columns `text-right`; optional `rowClassName(row)` for muted/canceled rows (`opacity-60`) | order cards (`orderCard.tsx`), cohort, follower and control tables, run history |
| `Tag` | daisyUI `badge badge-sm rounded-sm` plus tone: gain `bg-gain-bg text-gain-text border-0`, loss `bg-loss-bg text-loss-text border-0`, neutral `bg-raised text-ink-2 border-0`, accent `bg-accent-bg text-ticker border-0`, info `bg-[#0e1f33] text-[#79b8ff] border-0` | decision/verdict pills, order status, Filled / Not submitted |
| `TickerChip` | `font-mono text-[11px] font-semibold text-ticker bg-accent-bg px-1.5 py-0.5 rounded-sm` | every ticker cell |
| `SearchInput` | `<label>` + daisyUI `input input-sm w-56 bg-base-100`, `type="search"`, an `aria-label` when there's no visible label | Newsfeed + Decisions panels on Live, Decisions list, Company News |
| `Segmented` | daisyUI `join` of `btn btn-xs join-item`; active item `btn-active` with `bg-accent-bg text-primary border-accent-line` | verdict filter, chart range (1W/1M/3M/All), news scope |
| `Select` | daisyUI `select select-sm` with a small `text-[11px] text-dim` label above | Research filters, "Stopped at", windows |
| `Meter` | daisyUI `progress progress-primary h-2` (or `progress-success` for met prerequisites), with label and `n / max` text | guardrail progress, rollout prerequisites, cohort enrollment |
| `Collapse` | native `<details>` with daisyUI `collapse collapse-arrow` | full strategist / manager text |
| `charts/EquityChart` | Recharts `ComposedChart` + `Area` (fill `chartTheme.accent`, opacity 0.08) + `Line`, `type="linear"`; `ReferenceDot` per trade marker | `Graph.tsx` |
| `charts/DrawdownChart` | Recharts `Area`, stroke/fill `chartTheme.loss` | new, derived from equity points |
| `charts/DailyPnlChart` | Recharts `Bar` with per-`Cell` fill gain/loss | new, derived from equity points |
| `charts/LagScatter` | Recharts `ScatterChart`, `ReferenceArea` for the first hour, `ReferenceLine` at the median | Live + News lag page |

All charts: grid `chartTheme.grid`, axis text `chartTheme.dim` at 11px, no
smoothing (the current `monotone` spline invents curvature between points),
wrapped in `ResponsiveContainer`, and each chart's container gets
`role="img"` plus an `aria-label` that states the takeaway.

---

## 4. Shell: `src/App.tsx`, `src/App.css`, `src/routes.tsx`

Convert the shell to Tailwind and delete `App.css` in the same PR.

- **Top bar** (`bg-head border-b border-base-300`): brand mark
  (`size-3.5 rounded-sm bg-primary`) + name, page nav with a 2px
  `border-primary` underline on the active page, then market state and a
  `PAPER` tag (accent tone) on the right. Remove the bell/settings/user icon
  buttons until they do something.
- **Status sub-bar** (new, under the top bar, every page): per-component pills
  from `/api/dashboard-kpis` → `bot_status.detail` and `/api/script-status`
  (`state`, `stage`, `updated_at`): "News collector running · Worker paused ·
  Option manager paused · EOD sync paused · Last run 3:38 PM · No errors".
  Dots: running `bg-gain`, paused hollow `border border-dim`. This replaces the
  green "Healthy" card that contradicted its own detail text.
  The API already returns `bot_status.components` (`name`, `state`,
  `recent_error`); the sub-bar reads that, not the prose `detail`.
- **Remove the Live sidebar.** It's scroll anchors for one page and leaves the
  right third of the screen empty. Delete the `sections` key from the Live tab;
  `dashboardSections.ts` and `useActiveSection.ts` can go once nothing uses them.
- **Remove the yellow "Now · Current Overview 1/5" floating pill.** It covers
  the chart axis.
- **Sub-tabs** stay as the second strip: daisyUI `tabs tabs-border tabs-sm`,
  active tab underlined in `primary`.
- **Demo Mode** stays a text button at the end of the nav: `btn btn-ghost btn-xs`.
- **New route `system`** (see § 7, Extraction).

Route changes in `routes.tsx` (keep old slugs in `ALIASES` so bookmarks work):

```ts
decisions: tabs → 'decisions' (merged ledger+autopsy), 'news', 'map'
  ALIASES: 'decisions/ledger' → 'decisions/decisions',
           'decisions/autopsy' → 'decisions/decisions'
research:  tabs → 'expectancy', 'news-lag', 'backtests', 'followers', 'rollout'
system:    tabs → 'health' (new), 'extraction' (moved), 'risk' (moved)
  ALIASES: 'research/extraction' → 'system/extraction'
```

---

## 5. Live → Overview: `src/dashboardHome.tsx`  (mockup: `Main.dc.html`)

New order, top to bottom:

| # | Section | Component / file | Data |
| - | --- | --- | --- |
| 1 | Runtime stats strip | `StatStrip` in `dashboardKpis.tsx` | `/api/dashboard-kpis`: `account_equity`, `day_pl`/`day_pl_pct`, `performance_summary.total_return_pct`, unrealized (Needs), `options_exposure.market_value` as Holdings, `buying_power`, `max_drawdown_pct`, `win_rate` as "0 / 2", expectancy (`performance_summary.average_trade_return_pct`), `market_regime.state` |
| 2 | **Latest decision** | `whyBotTraded.tsx` (rewrite) | `/api/why-bot-traded` |
| 3 | Equity + drawdown + daily P/L | `Graph.tsx` → `EquityChart`, plus `DrawdownChart`, `DailyPnlChart` | `/api/portfolio-history` (`displayPreviousorder.ts`); markers from `/api/executed-trades` |
| 4a | Newsfeed (compact, searchable) | new `liveNewsfeed.tsx` | `/api/opportunist-company-news` (company scope, newest first, 5–8 rows) |
| 4b | Decisions (compact, searchable) | new `liveDecisions.tsx` | `/api/company-decisions` (latest run, newest first) |
| 5a | Orders | `orderCard.tsx` → `DataTable` | `/api/executed-trades` |
| 5b | News lag vs option P/L | `charts/LagScatter` | `/api/news-lag` → `trades[]` (`lag_hours`, `option_pnl_pct`) |
| 6 | Holdings · Data pipeline · Safeguards | `openPositionsTable.tsx` (compact), new small panels | `/api/open-positions`, `/api/news-extraction?days=7`, `/api/v2-status` + `thesis_validator` |

Layout: the page is `flex flex-col gap-3`; rows 3–6 are `flex flex-wrap gap-3`
with the wide panel `flex-[2_1_640px]` and the narrow one `flex-[1_1_360px]`.

**Moved off Live:** Option take-profit / stop-loss ladders and
`riskControls.tsx` → System › Risk. "People using it" (`usage_summary`) →
System › Health. Sector/industry rankings (`top_rankings`) → Decisions page
sidebar or Research. Thesis validator card → the Safeguards panel (one line).
`scriptStatus.tsx` "Main Script: Paused" card → the status sub-bar.

### Latest decision panel (most important panel on the page)

Two columns. Left: ticker chip, company, `Strategist [verdict] → Manager
[verdict]`, then the **manager's reason in full** (`text-[15px] leading-relaxed
max-w-[80ch]`, the text the user reads most), then For / Against chips
(`Tag` gain / loss). Right (`bg-head`): the **selected contract** block:
contract (`ORCL 20 Nov $121P`, `font-mono text-[17px] font-semibold`), type,
expiry · DTE, qty, fill, take-profit and stop-loss for that DTE bucket. When
nothing was selected: "None selected", where it stopped, and the last contract
actually traded. Status tag top right: Filled (gain) / Not submitted (neutral).

Today's payload: `ran_at, has_decision, ticker, decision, confidence, reason,
selected_contract, rejected_because, submitted`.
**Needs** (in `api_routes/dashboard.py`): `company_name`,
`strategist_decision`, `strategist_confidence`, `stopped_at`
(`'strategist'|'manager'|'safeguard'|null`), `contract: { symbol, type,
expiration_date, dte, strike }`, `quantity`, `fill_price`, `exit_plan: {
take_profit_pct, stop_loss_pct, dte_bucket }` (from the same DTE buckets
`option_exit_rules` already exposes), `factors_for: string[]`,
`factors_against: string[]`, `last_traded_contract`. For/Against chips are
optional: if the manager prompt doesn't emit structured factors, hide them and
show only the reason text.

### Search

Both 4a and 4b get a `SearchInput` in the `Panel` `actions` slot. Filter
client-side over the loaded rows (ticker, company name, headline, source);
show an empty row "No decisions for "x" today · search all history" that links
to the Decisions page with `?q=`. The Decisions page and Company News read the
same `q`.

### Orders table

Columns: Submitted · Contract (formatted, not the raw OCC string) · Type
(`Entry · buy put`, `Trailing profit stop · sell`) · Fill · Status. Canceled
rows `opacity-60`. Group an entry and its exit into one "trade" row when
`/api/executed-trades` exposes a link between them. **Needs:** `fill_price`,
`side`, and a `position_id`/`parent_order_id` to pair entry and exit.

### Equity chart

Start at the first funded day, not 0 (today the line jumps from $0 to $3k).
Default range 1W on trading days; 1M / 3M / All in a `Segmented`. Entry
markers hollow `chartTheme.info`, exit markers filled `chartTheme.ink`.
Drawdown and daily P/L are computed client-side from the same points, no new
endpoint.

---

## 6. Decisions: merge Ledger + Autopsy  (mockup: `Decisions.dc.html`)

New file `src/decisions.tsx` built from `companyDecisions.tsx` +
`decisionAutopsy.tsx` (delete `companyDecisions.css` and
`decisionAutopsy.css` with them). Layout: summary chips (evaluated / call /
put / no trade / executed), one filter toolbar (`SearchInput`, verdict
`Segmented`, "Stopped at" `Select`, date range), then list on the left
(`flex-[1_1_320px]`), detail on the right (`flex-[999_1_640px]`).

**List row:** a full-width `<button>` per decision: ticker, company, time,
`S [verdict] → M [verdict]` tags, one line of why (manager reason truncated,
or the blocking gate). Selected row `bg-raised border-base-300`.

**Detail, top to bottom:**
1. Header: ticker · verdict, company, time, snapshot id, model (`font-mono`),
   confidence and execution tags.
2. **Decision path** stepper: News → Strategist → Manager → Safeguards → Order,
   as an `<ol>` grid (`grid-cols-[repeat(auto-fit,minmax(140px,1fr))]`). Each
   step passed / stopped here / also blocked / not reached. Sources:
   `strategist.decision`, `manager.decision`, `constraints.entry_gate.allowed`,
   `constraints.market_regime_gate.allowed`, `outcome.trade_executed`,
   `v2_verdicts[]`.
3. Strategist (thesis bullets, risk chips) and Manager (reason, For / Against
   chips) side by side; full text in a `Collapse`.
4. **"What if it had traded?"** counterfactual for no-trade decisions:
   `attribution.horizon_returns` (already in the autopsy payload) for 1d/3d/5d
   stock move, plus the hypothetical contract and its P/L under the normal exit
   rules, and a verdict "block saved money / cost money" once resolved.
   **Needs:** hypothetical contract + P/L from the attribution runner; until
   then show the stock move only.
5. Gates table (gate · policy · mode · result) and Evidence cited (headline,
   source, age at decision time).

Autopsy's "Outcome & Review" column with mostly "Unavailable" goes away for
no-trade decisions; it's replaced by the counterfactual.

**Company News** (`analyzedCompanyNews.tsx`): filters collapse into one toolbar
row (`SearchInput` · scope `Segmented` · confidence `Select` · per-page),
scope cards with a 0 count are hidden, articles render as a dense feed (time ·
`TickerChip` · headline · source · evidence score bar).

**Exposure Map** (`exposureMap.tsx`): restyle only; don't show the empty graph
canvas until an article is analyzed, show the prompt in its place.

---

## 7. Research

All five pages share one header pattern: title (`text-lg font-bold`) +
one-line description + "as of" + filters as `Select`s on the right, then a
`StatStrip`, then a one-line warning if any, then charts, then tables.

| Page | File | Charts to add | Tables | Data |
| --- | --- | --- | --- | --- |
| Expectancy (`ResearchExpectancy.dc.html`) | `decisionExpectancy.tsx` | expectancy bar per cohort (dashed outline when `pnl_sample_count` = 0), `Meter` per cohort toward `min_samples`, resolved vs unresolved bar | cohorts (`DataTable`, right-aligned numbers) | `/api/decision-expectancy` → `summary`, `cohorts[]` (`insufficient_sample` drives muting) |
| News lag (`ResearchNewsLag.dc.html`) | `newsLag.tsx` | `LagScatter` with the first-hour band and median line; before/after split bar from `headline.median_pre_entry_share`; 21-cell grid for `favourable_but_lost` of `favourable_closed` | per-entry path table (keep the existing bar; colors `chartTheme.neutral` before entry, `chartTheme.info` our way, `chartTheme.accent` against us) | `/api/news-lag` → `headline`, `trades[]`, `coverage` |
| Backtests (`ResearchBacktests.dc.html`) | `backtest.tsx` | run detail: cumulative P/L from `metrics.equity_curve` (already returned), "where decisions went" bars (`filled_count`, `portfolio_rejected_count`, `no_fill_count`), expectancy-by-run bars across `runs[]` | run history as a `DataTable`, selected row highlighted; config as one compact fieldset row of `input input-xs font-mono` | `/api/backtests`, `/api/backtests/:id` |
| Followers (`ResearchFollowers.dc.html`) | `followerAccounts.tsx` | grouped bars: each production entry × vehicle return; diverging bars: each account's difference vs production with `n=` | per account, recent trades | `/api/follower-accounts` |
| V2 rollout (`ResearchRollout.dc.html`) | `v2Status.tsx`, `forwardEvaluation.tsx` | **would-block rate per control** (bar = would_block / observed, `bg-primary` when requested enforce, `bg-[#6e6e76]` otherwise); prerequisite `Meter`s | controls (requested vs effective, proof, would block); forward cohorts with enrollment meters; discovery ledger as stacked bars | `/api/v2-status`, `/api/forward-evaluation` |

The would-block chart is the headline of the rollout page: provenance and
regime currently block 100% of what they see, so enforcing either as-is would
stop all trading. Put a one-line note under the chart saying so when any
requested-enforce control is ≥ 95%.

**Extraction** (`newsExtraction.tsx`) moves to System › Extraction unchanged
apart from styling; its headline numbers (success rate, URLs saved, sources)
appear in the Live "Data pipeline" panel.

---

## 8. Mobile  (mockup: `Phone.dc.html`)

At < 640px (`max-sm:`) the Live page collapses to: status line → equity card
with a sparkline and day P/L → today's pipeline counts (evaluated → call/put →
filled) → latest decision (manager reason + contract line) → last trade, with
a bottom nav of Live / Decisions / Research (daisyUI `dock`, `sm:hidden`).
Tables scroll horizontally inside their panel; no separate mobile build is
needed.

---

## 9. Backend fields this needs (all additive)

| Endpoint (`api_routes/`) | Add |
| --- | --- |
| `dashboard.py` `/api/dashboard-kpis` | `unrealized_pl` |
| `dashboard.py` `/api/why-bot-traded` | fields listed in § 5 "Latest decision" |
| `trades.py` `/api/executed-trades` | `fill_price`, `side`, `quantity`, `position_id` (pairs entry/exit) |
| `dashboard.py` / pipeline | today's funnel counts: evaluated, strategist candidates, manager call/put, passed safeguards, filled |
| `decision_autopsy.py` | counterfactual contract + P/L in `attribution` (stock move already there) |
| `/api/opportunist-company-news` | an evidence score per article (for the feed's score bar); `section_article_counts` for every scope, not only the requested one |

None of these block the visual work: build each panel against today's payload
and hide the parts whose fields are missing.

---

## 10. Suggested order (one PR each)

1. **Setup:** install Tailwind + daisyUI, Vite plugin, new `index.css` with
   the theme, `@theme` tokens and the temporary legacy block,
   `src/ui/chartTheme.ts`. No component changes; fix whatever Preflight
   shifts.
2. `src/ui/` shared components, then the shell (top bar, status sub-bar, no
   sidebar, no floating pill); delete `App.css`. The shell's container owns
   page width, so legacy `width: min(…px, …vw)` page rules became `100%`.
3. Live page (§ 5), latest-decision panel first; delete each converted
   component's `.css`. The `charts/*` components land here and in step 5,
   with the first page that feeds them data. Done; it also brought forward
   the System route (Health, Risk, and Extraction with its
   `research/extraction` alias) so what left Live had somewhere to go, and
   made `/api/dashboard-kpis` one shared request per page: the endpoint is
   slow and served one request at a time, so per-panel copies queued into
   504s.
4. Decisions merge (§ 6) with route aliases. The page is `decisionsPage.tsx`
   with its logic in `decisions.ts` (not `decisions.tsx`, which would clash
   on import). It reads only `/api/decision-autopsies`, plus
   `/api/company-decisions` for the one thing the Ledger had that the
   autopsy lacks: strategist attempts and price-history loads that failed
   before any decision was recorded, folded into one warning line.
   `attribution.horizon_returns` is keyed by trading days `"5"` and `"10"`
   and holds fractions, so the counterfactual shows 5d / 10d, not 1d/3d/5d.
   Risks render as a list, not chips: they are full sentences. Pages read a
   linked search from `?q=` (`searchFromHash`).
   Company News: logic in `companyNews.ts`, one flat feed newest first, 50
   rows at a time (a page of 5 companies carried ~1,000 article cards). One
   search box filters loaded rows as you type and looks the company up on
   Enter. All four scopes stay visible: the API fills in only the active
   scope's count, so hiding zero counts would hide every other scope. No
   evidence score bar: the payload has no score (§ 9).
   Exposure Map: filters and legend also wait for an analyzed article. Its
   nodes never rendered React Flow handles, so no edge had ever drawn; they
   now carry hidden ones. Loading is derived rather than set in effects.
5. Research pages (§ 7), then System route. Done. Expectancy pools its
   cohorts into the stat strip (sample-weighted, and only when the cohorts
   add up to the summary). Backtests' "where decisions went" counts event
   statuses: the metrics' `no_fill_count` already includes guardrail
   rejects. Followers follows its mockup: grouped bars per mirrored entry and
   a difference-vs-production chart replace the cumulative line chart. The
   rollout page leads with a stat strip, so `v2Safeguards.tsx` is gone (Live
   has its own safeguards panel); prerequisites become meters only when their
   value carries a count ("31/37 closed"). Risk settings show open on their
   own page instead of a collapsed `<details>`. `DataTable` takes a `label`
   so pages with several tables name each one.
6. Backend fields (§ 9) and wiring the placeholders. Done for executed
   trades, unrealized P/L, the latest decision and company news:
   `fill_price` already existed in the table; `position_id` pairs a sell with
   the latest filled buy of the same contract (exits store no link, so an
   exit whose entry is older than the loaded window stays unpaired), and the
   Orders table shows one row per trade. `side` and `quantity` were already
   there as `order_side` / `order_qty`. The latest decision's `exit_plan`
   comes from the option manager's own DTE resolver; the manager emits no
   structured factors, so For/Against stays hidden. Company news loads all
   four scopes so every count is real and ships only the viewed scope's
   articles. Not done: today's funnel counts (no panel reads them until the
   § 8 phone layout exists; the Decisions page counts client-side) and the
   autopsy's counterfactual contract + P/L, which needs a pricing choice.
7. Delete the temporary legacy variable block from `index.css`. Done with
   step 5: no `.css` file besides `index.css` remains. Only `--app-chrome`
   survives, which the Exposure Map uses to size itself.

**Check each PR with:** `npm test`, `npm run build` and `npm run lint` in `web_dashboard/`,
demo mode on the views that support it (`#/decisions/map?demo=1`,
`#/research/rollout?demo=1`, autopsy), old hash URLs still resolving, and the
page at 390px wide.
