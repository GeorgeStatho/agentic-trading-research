# Dashboard redesign

Spec for restyling `web_dashboard/` into a dense, chart-heavy research terminal:
QuantConnect's runtime-stats banner and stacked charts, Benzinga Pro's compact
data tables, on a charcoal + orange palette. Mockups live in
[`mockups/`](mockups/README.md); this file maps them onto the real code.

Scope is **presentation first**. Every page keeps its current endpoint and
data flow unless a section below lists a new field under "Needs".

---

## 1. Ground rules

These apply to every page and are the main thing a reviewer should check.

1. **One accent.** Orange (`--accent`) marks the active nav item, links, the
   primary button, the equity line and "requested enforce". Nothing else.
2. **Red and green mean money.** Use `--loss` / `--gain` for P/L, pass/block
   and errors only. "Market closed" and "high volatility" are neutral, not red.
3. **Sentence-case labels.** Drop the uppercase/monospace eyebrow labels
   (`ACCOUNT EQUITY`, `WHY DID THE BOT TRADE?`). Labels are 11px, `--text-dim`.
4. **Numbers line up.** `font-variant-numeric: tabular-nums` on `:root`;
   tickers, contracts, timestamps and run ids in `--mono`.
5. **Density.** 12–13px body, 6px radius, 12px gaps, panels with a thin header
   bar. No card-inside-card.
6. **No banner walls.** The full-width yellow notice boxes become a single line
   of `--warning` text under the stats strip. Keep the text; lose the box.
7. **Show sample size.** Any rate or average computed from fewer than the
   page's guardrail (5 closed trades) shows `n=` and is muted. Sharpe/Sortino
   are hidden until n ≥ 20.
8. **One page width.** Every page uses the same `max-width: 1400px` container.
   Today Live, Decisions and Research each use different widths.
9. **Phone width works.** Rows are `flex-wrap` with `flex-basis` minimums,
   wide tables scroll inside `overflow-x: auto`, nothing uses `100vh` sidebars.

---

## 2. Tokens: `src/index.css`

Keep the existing variable **names** so current component CSS recolors for
free, and change their **values**. Then add the new semantic tokens.

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
  /* surfaces */
  --background: #121214;
  --surface: #121214;
  --surface-container-low: #161618;   /* header, sub-bars, table head */
  --surface-container: #1a1a1d;       /* panels */
  --surface-container-high: #232327;  /* row dividers, chips, selected row */
  --surface-container-highest: #2c2c31;
  --outline-variant: #2c2c31;         /* panel borders */
  /* text */
  --on-surface: #e8e8ea;
  --on-surface-variant: #c2c2c8;
  --text-dim: #9a9aa2;                /* labels; 6:1 on panels */
  /* accent (QuantConnect orange) */
  --primary: #f98309;
  --primary-container: #24180b;
  --accent-bg: #24180b;
  --accent-border: #5a3510;
  --accent-hover: #ffa347;
  /* money + status */
  --gain: #3fb950;      --gain-text: #56d364;  --gain-bg: #12261a;
  --loss: #f85149;      --loss-text: #ff9b94;  --loss-bg: #2d1617;
  --warning: #ffb066;
  --info: #58a6ff;      /* trade entries, "our way" moves */
  /* legacy names, re-pointed */
  --secondary: var(--gain);
  --tertiary: var(--loss-text);
  --tertiary-container: var(--loss);
  --text: var(--on-surface-variant);
  --text-h: var(--on-surface);
  --bg: var(--background);
  --border: var(--outline-variant);
  --accent: var(--primary);
  --radius: 6px;
  --sans: 'Inter', system-ui, sans-serif;
  --heading: 'Inter', system-ui, sans-serif;
  --mono: 'JetBrains Mono', ui-monospace, monospace;
  font: 13px/1.4 var(--sans);
  font-variant-numeric: tabular-nums;
}
```

After the swap, grep for hard-coded colors left in component CSS
(`grep -rnE '#[0-9a-fA-F]{6}|rgba\(' src/*.css`) and replace them with tokens;
`App.css`, `kpi.css`, `whyBotTraded.css` and `card.css` have the most.

Alternative palette "Deep Teal" (not applied) is in `mockups/Palette.dc.html`
if the orange is ever swapped: ground `#061012`, panel `#0c1a1d`, accent
`#2dd4bf`, gain `#a3e635`, loss `#fb7185`.

---

## 3. Shared components (new: `src/ui/`)

The repo uses one `.tsx` + one `.css` per component; keep that. These replace
the ad-hoc card markup repeated across pages.

| Component | Props (sketch) | Replaces / used by |
| --- | --- | --- |
| `Panel` | `title`, `meta?`, `actions?`, `children`, `flush?` (no body padding, for tables) | every `*-panel` section wrapper |
| `StatStrip` | `items: { label, value, sub?, tone?: 'gain'\|'loss'\|'muted' }[]` | KPI cards (`dashboardKpis.tsx`) and the 4-card summary rows on every Research page |
| `DataTable` | `columns`, `rows`, `empty`, `minWidth`, `rowTone?` | order cards, cohort tables, follower tables, control tables |
| `Tag` | `tone: 'gain'\|'loss'\|'neutral'\|'accent'\|'info'`, `children` | decision/verdict pills, order status |
| `TickerChip` | `symbol` | every ticker cell (orange mono on `--accent-bg`) |
| `SearchInput` | `value`, `onChange`, `placeholder`, `label` | Newsfeed + Decisions panels on Live, Decisions list, Company News |
| `Meter` | `value`, `max`, `tone?`, `label` | guardrail progress, rollout prerequisites, cohort enrollment |
| `charts/EquityChart` | `points`, `markers: { at, kind: 'entry'\|'exit', label }[]`, `range` | `Graph.tsx` |
| `charts/DrawdownChart` | `points` | new, derived from equity points |
| `charts/DailyPnlChart` | `points` | new, derived from equity points |
| `charts/LagScatter` | `trades` | Live + News lag page |

Charts use **Recharts** (already a dependency): `ComposedChart` + `Area` +
`Line` for equity with `ReferenceDot` markers; `Area` for drawdown; `Bar` with
per-cell `fill` for daily P/L; `ScatterChart` for lag. Chart colors come from
the tokens; grid lines `#26262b`; no smoothing (`type="linear"` or
`"stepAfter"`) because the current `monotone` spline invents curvature between
points.

---

## 4. Shell: `src/App.tsx`, `src/App.css`, `src/routes.tsx`

- **Top bar:** brand mark (14px orange square) + name, page nav with an orange
  2px underline on the active page, then market state and a `PAPER` badge on
  the right. Replace the bell/settings/user icon buttons with nothing until
  they do something.
- **Status sub-bar** (new, under the top bar, every page): per-component pills
  from `/api/dashboard-kpis` → `bot_status.detail` and `/api/script-status`
  (`state`, `stage`, `updated_at`): "News collector running · Worker paused ·
  Option manager paused · EOD sync paused · Last run 3:38 PM · No errors".
  This replaces the green "Healthy" card that contradicted its own detail text.
  **Needs:** `bot_status` returned as a list of `{ component, state }` instead
  of one prose `detail` string.
- **Remove the Live sidebar.** It is scroll anchors for one page and leaves the
  right third of the screen empty. Delete the `sections` key from the Live tab;
  `dashboardSections.ts` and `useActiveSection.ts` can go once nothing uses them.
- **Remove the yellow "Now · Current Overview 1/5" floating pill.** It covers
  the chart axis.
- **Sub-tabs** stay as the second strip (`page-tabs`), restyled like the
  mockups: 12px, orange underline.
- **Demo Mode** stays a text button at the end of the nav, neutral style.
- **New route `system`** (see § Research → Extraction).

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

**Moved off Live:** Option take-profit / stop-loss ladders and
`riskControls.tsx` → System › Risk. "People using it" (`usage_summary`) →
System › Health. Sector/industry rankings (`top_rankings`) → Decisions page
sidebar or Research. Thesis validator card → the Safeguards panel (one line).
`scriptStatus.tsx` "Main Script: Paused" card → the status sub-bar.

### Latest decision panel (most important panel on the page)

Two columns. Left: ticker chip, company, `Strategist [verdict] → Manager
[verdict]`, then the **manager's reason in full at 15px** (this is the text
the user reads most), then For / Against chips. Right: the **selected
contract** block: contract (`ORCL 20 Nov $121P`), type, expiry · DTE, qty,
fill, take-profit and stop-loss for that DTE bucket. When nothing was
selected: "None selected", where it stopped, and the last contract actually
traded. Status tag top right: Filled (gain) / Not submitted (neutral).

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

Both 4a and 4b get a `SearchInput` in the panel header. Filter client-side over
the loaded rows (ticker, company name, headline, source); show an empty row
"No decisions for "x" today · search all history" that links to the Decisions
page with `?q=`. The Decisions page and Company News read the same `q`.

### Orders table

Columns: Submitted · Contract (formatted, not the raw OCC string) · Type
(`Entry · buy put`, `Trailing profit stop · sell`) · Fill · Status. Canceled
rows at 60% opacity. Group an entry and its exit into one "trade" row when
`/api/executed-trades` exposes a link between them. **Needs:** `fill_price`,
`side`, and a `position_id`/`parent_order_id` to pair entry and exit.

### Equity chart

Start at the first funded day, not 0 (today the line jumps from $0 to $3k).
Default range 1W on trading days; 1M / 3M / All buttons. Entry markers hollow
`--info`, exit markers filled `--on-surface`. Drawdown and daily P/L are
computed client-side from the same points, no new endpoint.

---

## 6. Decisions: merge Ledger + Autopsy  (mockup: `Decisions.dc.html`)

New file `src/decisions.tsx` built from `companyDecisions.tsx` +
`decisionAutopsy.tsx`. Layout: summary chips (evaluated / call / put / no trade
/ executed), one filter toolbar (search, verdict segmented control, "Stopped
at" select, date range), then list on the left, detail on the right.

**List row:** ticker, company, time, `S [verdict] → M [verdict]` tags, one line
of why (manager reason truncated, or the blocking gate).

**Detail, top to bottom:**
1. Header: ticker · verdict, company, time, snapshot id, model (mono),
   confidence and execution tags.
2. **Decision path** stepper: News → Strategist → Manager → Safeguards → Order.
   Each step passed / stopped here / also blocked / not reached. Sources:
   `strategist.decision`, `manager.decision`, `constraints.entry_gate.allowed`,
   `constraints.market_regime_gate.allowed`, `outcome.trade_executed`,
   `v2_verdicts[]`.
3. Strategist (thesis bullets, risk chips) and Manager (reason, For / Against
   chips) side by side; full text in `<details>`.
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
row (search · scope segmented control · confidence select · per-page), scope
cards with a 0 count are hidden, articles render as a dense feed (time ·
ticker chip · headline · source · evidence score bar).

**Exposure Map** (`exposureMap.tsx`): restyle only; don't show the empty graph
canvas until an article is analyzed, show the prompt in its place.

---

## 7. Research

All five pages share one header pattern: title + one-line description + "as
of" + filters as compact selects on the right, then a `StatStrip`, then a
one-line warning if any, then charts, then tables.

| Page | File | Charts to add | Tables | Data |
| --- | --- | --- | --- | --- |
| Expectancy (`ResearchExpectancy.dc.html`) | `decisionExpectancy.tsx` | expectancy bar per cohort (dashed outline when `pnl_sample_count` = 0), `Meter` per cohort toward `min_samples`, resolved vs unresolved bar | cohorts (`DataTable`, right-aligned numbers) | `/api/decision-expectancy` → `summary`, `cohorts[]` (`insufficient_sample` drives muting) |
| News lag (`ResearchNewsLag.dc.html`) | `newsLag.tsx` | `LagScatter` with the first-hour band and median line; before/after split bar from `headline.median_pre_entry_share`; 21-cell grid for `favourable_but_lost` of `favourable_closed` | per-entry path table (keep the existing bar, colors: before `#6e6e76`, our way `--info`, against `--accent`) | `/api/news-lag` → `headline`, `trades[]`, `coverage` |
| Backtests (`ResearchBacktests.dc.html`) | `backtest.tsx` | run detail: cumulative P/L from `metrics.equity_curve` (already returned), "where decisions went" bars (`filled_count`, `portfolio_rejected_count`, `no_fill_count`), expectancy-by-run bars across `runs[]` | run history as a table, selected row highlighted; config as one compact fieldset row | `/api/backtests`, `/api/backtests/:id` |
| Followers (`ResearchFollowers.dc.html`) | `followerAccounts.tsx` | grouped bars: each production entry × vehicle return; diverging bars: each account's difference vs production with `n=` | per account, recent trades | `/api/follower-accounts` |
| V2 rollout (`ResearchRollout.dc.html`) | `v2Status.tsx`, `forwardEvaluation.tsx` | **would-block rate per control** (bar = would_block / observed, orange when requested enforce); prerequisite `Meter`s | controls (requested vs effective, proof, would block); forward cohorts with enrollment meters; discovery ledger as stacked bars | `/api/v2-status`, `/api/forward-evaluation` |

The would-block chart is the headline of the rollout page: provenance and
regime currently block 100% of what they see, so enforcing either as-is would
stop all trading. Put a one-line note under the chart saying so when any
requested-enforce control is ≥ 95%.

**Extraction** (`newsExtraction.tsx`) moves to System › Extraction unchanged
apart from styling; its headline numbers (success rate, URLs saved, sources)
appear in the Live "Data pipeline" panel.

---

## 8. Mobile  (mockup: `Phone.dc.html`)

At < 640px the Live page collapses to: status line → equity card with a
sparkline and day P/L → today's pipeline counts (evaluated → call/put →
filled) → latest decision (manager reason + contract line) → last trade, with
a bottom nav of Live / Decisions / Research. Tables scroll horizontally inside
their panel; no other separate mobile build is needed.

---

## 9. Backend fields this needs (all additive)

| Endpoint (`api_routes/`) | Add |
| --- | --- |
| `dashboard.py` `/api/dashboard-kpis` | `unrealized_pl`; `bot_status.components: [{ component, state }]` |
| `dashboard.py` `/api/why-bot-traded` | fields listed in § 5 "Latest decision" |
| `trades.py` `/api/executed-trades` | `fill_price`, `side`, `quantity`, `position_id` (pairs entry/exit) |
| `dashboard.py` / pipeline | today's funnel counts: evaluated, strategist candidates, manager call/put, passed safeguards, filled |
| `decision_autopsy.py` | counterfactual contract + P/L in `attribution` (stock move already there) |

None of these block the visual work: build each panel against today's payload
and hide the parts whose fields are missing.

---

## 10. Suggested order (one PR each)

1. Tokens in `index.css` + font swap + hard-coded color cleanup.
2. `src/ui/` shared components, then the shell (top bar, status sub-bar, no
   sidebar, no floating pill).
3. Live page (§ 5), latest-decision panel first.
4. Decisions merge (§ 6) with route aliases.
5. Research pages (§ 7), then System route.
6. Backend fields (§ 9) and wiring the placeholders.

**Check each PR with:** `npm run build` and `npm run lint` in `web_dashboard/`,
demo mode on the views that support it (`#/decisions/map?demo=1`,
`#/research/rollout?demo=1`, autopsy), old hash URLs still resolving, and the
page at 390px wide.
