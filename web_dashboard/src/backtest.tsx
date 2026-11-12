import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { createRun, dataGaps, decisionOutcomes, fetchEodQuotes, getRun, getRuns, runKind, sortRuns } from './backtests'
import type { BacktestEvent, DecisionWindow, Detail, EodQuoteFetch, ReplaySummary, Run, RunKind, RunSortKey } from './backtests'
import { formatCurrency, formatDate, formatDateTime, formatShortDateTime, formatSignedCurrency, formatSignedPercent, formatToken, formatTokenList, hasNumericValue } from './format'
import PageHeader from './pageHeader'
import { chartTheme } from './ui/chartTheme'
import { AXIS_LINE, AXIS_TICK, TOOLTIP_STYLE } from './ui/charts/axis'
import type { Column } from './ui/DataTable'
import { DataTable } from './ui/DataTable'
import { Panel } from './ui/Panel'
import { StatStrip } from './ui/StatStrip'
import type { Stat } from './ui/StatStrip'
import { Tag } from './ui/Tag'
import { TickerChip } from './ui/TickerChip'

/** Backtest tables print an em dash rather than "N/A" for metrics with no samples. */
const EMPTY = '—'
/** Below this many fills a run's rates are shown muted, with n=. */
const SAMPLE_GUARDRAIL = 5

function percent(value: number | null | undefined): string {
  return formatSignedPercent(value, { placeholder: EMPTY })
}

function dollars(value: number | null | undefined): string {
  return formatCurrency(value, { placeholder: EMPTY })
}

function when(value: string | null | undefined): string {
  return formatDateTime(value ?? '', { placeholder: 'Unknown' })
}

function pnlClass(value: number | null | undefined): string {
  if (!hasNumericValue(value) || value === 0) return ''
  return value < 0 ? 'text-loss' : 'text-gain'
}

function expectancyTone(value: number | null | undefined, filled: number): Stat['tone'] {
  if (filled < SAMPLE_GUARDRAIL || !hasNumericValue(value)) return 'muted'
  return value < 0 ? 'loss' : 'gain'
}

function modelName(run: Run): string {
  return !run.model || run.model === 'stored' ? 'Stored decision model' : run.model
}

// ---- Settings ---------------------------------------------------------------

type ConfigKey = 'entry_slippage_bps' | 'exit_slippage_bps' | 'commission_per_contract' | 'take_profit_pct' | 'stop_loss_pct' | 'max_hold_hours' | 'max_concurrent_positions' | 'max_total_entry_notional' | 'max_sector_notional_pct' | 'daily_loss_limit_dollars' | 'max_spread_pct' | 'min_volume' | 'max_entry_delay_minutes' | 'default_order_qty'
type Field = { key: ConfigKey; label: string; min?: number; max?: number; step?: number; blank?: string }

// null leaves the setting to the API: take-profit and stop-loss then follow production's rule for each contract's DTE.
const DEFAULT_CONFIG: Record<ConfigKey, number | null> = {
  entry_slippage_bps: 25, exit_slippage_bps: 25, commission_per_contract: 0.65,
  take_profit_pct: null, stop_loss_pct: null, max_hold_hours: 168, max_concurrent_positions: 5,
  max_total_entry_notional: 50000, max_sector_notional_pct: 50, daily_loss_limit_dollars: 2000,
  max_spread_pct: 0.25, min_volume: 0, max_entry_delay_minutes: 1440, default_order_qty: 1,
}

const FIELD_GROUPS: { legend: string; fields: Field[] }[] = [
  { legend: 'Costs', fields: [
    { key: 'entry_slippage_bps', label: 'Entry slippage (bps)', min: 0 },
    { key: 'exit_slippage_bps', label: 'Exit slippage (bps)', min: 0 },
    { key: 'commission_per_contract', label: 'Commission / contract ($)', min: 0, step: 0.05 },
  ] },
  { legend: 'Risk limits', fields: [
    { key: 'take_profit_pct', label: 'Take profit (%)', min: 0, blank: 'DTE rule' },
    { key: 'stop_loss_pct', label: 'Stop loss (%)', max: 0, blank: 'DTE rule' },
    { key: 'max_hold_hours', label: 'Max hold (hours)', min: 1 },
    { key: 'max_concurrent_positions', label: 'Max positions', min: 1 },
    { key: 'max_total_entry_notional', label: 'Portfolio notional ($)', min: 1, step: 1000 },
    { key: 'max_sector_notional_pct', label: 'Sector cap (%)', min: 1, max: 100 },
    { key: 'daily_loss_limit_dollars', label: 'Daily loss limit ($)', min: 1, step: 100 },
  ] },
  { legend: 'Quote & entry', fields: [
    { key: 'max_spread_pct', label: 'Max spread (ratio)', min: 0.01, max: 1, step: 0.05 },
    { key: 'min_volume', label: 'Min volume', min: 0 },
    { key: 'max_entry_delay_minutes', label: 'Max entry delay (min)', min: 1, step: 60 },
    { key: 'default_order_qty', label: 'Order qty', min: 1 },
  ] },
]

/** Labels for a run's recorded config keys; unknown keys fall back to their token. */
const CONFIG_LABELS: Record<string, string> = {
  ...Object.fromEntries(FIELD_GROUPS.flatMap((group) => group.fields.map((field) => [field.key, field.label]))),
  option_multiplier: 'Option multiplier', risk_free_rate: 'Risk-free rate', data_source_versions: 'Data sources',
}
/** Shown in the run header already. */
const HEADER_CONFIG_KEYS = new Set(['strategy_version', 'model', 'prompt_version'])

function configValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return EMPTY
  return typeof value === 'object' ? Object.entries(value).map(([key, item]) => `${key}: ${String(item)}`).join(', ') || EMPTY : String(value)
}

/** A date input's local day as UTC instants; the API reads naive timestamps as UTC. */
function dayStart(day: string): string | null {
  return day ? new Date(`${day}T00:00`).toISOString() : null
}

function dayEnd(day: string): string | null {
  return day ? new Date(`${day}T23:59:59.999`).toISOString() : null
}

/** `blank` names what an empty field means; without it the field always holds a number. */
function NumberField({ label, value, onChange, min, max, step = 1, blank }: { label: string; value: number | null; onChange: (value: number | null) => void; min?: number; max?: number; step?: number; blank?: string }) {
  return <label className="flex flex-col gap-1">
    <span className="text-[11px] text-dim">{label}</span>
    <input type="number" className="input input-xs w-24 font-mono" min={min} max={max} step={step} value={value ?? ''} placeholder={blank}
      onChange={(event) => onChange(blank !== undefined && event.target.value === '' ? null : Number(event.target.value))} />
  </label>
}

function Fieldset({ legend, children }: { legend: string; children: ReactNode }) {
  return <fieldset className="flex flex-wrap items-end gap-2 rounded-box border border-base-300 px-3 pt-1 pb-2.5">
    <legend className="px-1 text-[11px] font-medium text-ink-2">{legend}</legend>
    {children}
  </fieldset>
}

// ---- Charts -----------------------------------------------------------------

/** Cumulative simulated P/L as a step line: equity only changes when a trade closes. */
function EquityCurve({ points }: { points: { at: string; pnl_dollars: number }[] }) {
  const data = points.map((point) => ({ at: point.at, pnl: point.pnl_dollars }))
  const last = data[data.length - 1]?.pnl ?? 0
  return <div role="img" aria-label={`Simulated cumulative P/L ${formatSignedCurrency(last)} after ${data.length} closes`} className="h-48">
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={chartTheme.grid} vertical={false} />
        <XAxis dataKey="at" tickFormatter={(value: string) => formatDate(value, { placeholder: '' })} tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} minTickGap={24} />
        <YAxis width="auto" tickFormatter={(value: number) => formatCurrency(value, { digits: 0 })} tick={AXIS_TICK} axisLine={false} tickLine={false} />
        <ReferenceLine y={0} stroke={chartTheme.axis} />
        <Tooltip {...TOOLTIP_STYLE} formatter={(value) => [formatSignedCurrency(Number(value)), 'Cumulative P/L']} labelFormatter={(label) => when(String(label))} />
        <Line type="stepAfter" dataKey="pnl" stroke={last < 0 ? chartTheme.loss : chartTheme.gain} strokeWidth={2} dot={{ r: 3, strokeWidth: 0, fill: last < 0 ? chartTheme.loss : chartTheme.gain }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  </div>
}

const OUTCOME_COLORS: Record<string, string> = { simulated_closed: chartTheme.gain, simulated_open: chartTheme.gain, portfolio_rejected: chartTheme.accent }

function OutcomeBars({ events }: { events: readonly BacktestEvent[] }) {
  const outcomes = decisionOutcomes(events)
  const largest = Math.max(1, ...outcomes.map((outcome) => outcome.count))
  return <div role="img" aria-label={`Where the ${events.length} decisions went: ${outcomes.map((outcome) => `${outcome.count} ${outcome.label}`).join(', ')}`} className="flex flex-col gap-2">
    {outcomes.map((outcome) => <div key={outcome.status} className="grid grid-cols-[7.5rem_1fr_2rem] items-center gap-2 text-[11px]">
      <span className="text-ink-2 first-letter:uppercase">{outcome.label}</span>
      <span className="h-3 rounded-sm" style={{ width: `${(outcome.count / largest) * 100}%`, background: OUTCOME_COLORS[outcome.status] ?? chartTheme.neutral }} />
      <span className="text-right">{outcome.count}</span>
    </div>)}
  </div>
}

/** Each run's expectancy as a column, oldest first; a grey stub where a run produced no fills. */
function ExpectancyByRun({ runs, selectedId }: { runs: readonly Run[]; selectedId: number | null }) {
  const ordered = [...runs].sort((left, right) => left.id - right.id)
  const values = ordered.map((run) => run.metrics.expectancy_pct ?? null)
  const top = Math.max(0, ...values.map((value) => value ?? 0))
  const bottom = Math.max(0, ...values.map((value) => -(value ?? 0)))
  const span = top + bottom || 1
  const label = ordered.map((run) => `#${run.id} ${hasNumericValue(run.metrics.expectancy_pct) ? percent(run.metrics.expectancy_pct) : 'no fills'}`).join(', ')
  return <div className="flex flex-col gap-1">
    <div role="img" aria-label={`Expectancy by run: ${label}`} className="flex h-36 gap-px">
      {ordered.map((run, index) => {
        const value = values[index]
        const ring = run.id === selectedId ? 'outline outline-1 outline-base-content' : ''
        return <div key={run.id} className="flex min-w-0 flex-1 flex-col" title={`#${run.id} · ${modelName(run)} · ${hasNumericValue(value) ? percent(value) : 'no fills'}`}>
          <div className="flex flex-col justify-end" style={{ height: `${(top / span) * 100}%` }}>
            {value !== null && value > 0 ? <div className={`rounded-t-sm bg-gain ${ring}`} style={{ height: `${(value / top) * 100}%` }} /> : null}
            {value === null ? <div className="h-0.5 bg-dim" /> : null}
          </div>
          <div className="flex flex-col border-t border-base-300" style={{ height: `${(bottom / span) * 100}%` }}>
            {value !== null && value < 0 ? <div className={`rounded-b-sm bg-loss ${ring}`} style={{ height: `${(-value / bottom) * 100}%` }} /> : null}
          </div>
        </div>
      })}
    </div>
    <div className="flex justify-between text-[11px] text-dim"><span>#{ordered[0]?.id}</span><span>oldest → newest · grey stub: no fills</span><span>#{ordered[ordered.length - 1]?.id}</span></div>
  </div>
}

// ---- Run detail panels ------------------------------------------------------

function RunSettings({ run }: { run: Run }) {
  const entries = Object.entries(run.config ?? {}).filter(([key]) => !HEADER_CONFIG_KEYS.has(key))
  if (!entries.length && !run.code_revision) return null
  return <Panel title="Run settings">
    <details>
      <summary className="cursor-pointer text-[11px] text-dim">{entries.length} recorded settings{run.code_revision ? ` · code ${run.code_revision.slice(0, 10)}` : ''}</summary>
      <dl className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-x-4 gap-y-1 text-[11px]">
        {entries.map(([key, value]) => <div key={key} className="flex justify-between gap-2">
          <dt className="text-dim">{CONFIG_LABELS[key] ?? formatToken(key)}</dt>
          <dd className="truncate font-mono" title={configValue(value)}>{configValue(value)}</dd>
        </div>)}
      </dl>
    </details>
  </Panel>
}

function Fact({ value, label }: { value: ReactNode; label: string }) {
  return <li><span className="font-mono font-semibold">{value}</span> {label}</li>
}

/** Why an LLM replay traded what it did: which decisions it saw, how its answers went, and what it overrode. */
function ReplaySummaryPanel({ replay }: { replay: ReplaySummary }) {
  const outcomes = Object.entries(replay.outcomes ?? {}).sort(([, left], [, right]) => right - left)
  const strategist = replay.strategist_replay
  const settings = replay.production_settings
  const cutoff = replay.contamination?.knowledge_cutoff
  const missingBars = replay.daily_bars?.still_missing ?? []
  return <Panel title="Replay summary">
    <div className="flex flex-col gap-2 text-xs text-ink-2">
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        <Fact value={replay.excluded_pre_knowledge_cutoff ?? 0} label={`excluded by knowledge cutoff${cutoff ? ` (${formatDate(String(cutoff))} + ${String(replay.contamination?.margin_days ?? '?')}d)` : ''}`} />
        <Fact value={replay.missing_frozen_input_count ?? 0} label="missing frozen input" />
        {replay.min_hours_between_same_symbol ? <Fact value={replay.spaced_out_decision_count ?? 0} label={`spaced out (${replay.min_hours_between_same_symbol}h per symbol)`} /> : null}
        <Fact value={replay.source_decision_count ?? 0} label="decisions replayed" />
      </ul>
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        <Fact value={replay.changed_direction_count ?? 0} label="changed direction" />
        <Fact value={replay.cached_answer_count ?? 0} label="cached answers" />
        <li className="text-dim">{replay.provider ?? 'unknown provider'} · sample {replay.sample ?? 0}</li>
      </ul>
      {outcomes.length ? <p>Outcomes: {outcomes.map(([outcome, count]) => `${count} ${formatToken(outcome)}`).join(' · ')}</p> : null}
      {strategist ? <p>Strategist replayed by {strategist.model} ({strategist.prompt_version}): {strategist.changed_decision_count} changed · {strategist.cached_answer_count} cached{strategist.reuse_max_age_minutes ? ` · ${strategist.reused_answer_count} reused (≤${strategist.reuse_max_age_minutes} min)` : ''}</p> : null}
      {replay.thesis_recheck ? <p>Thesis recheck: verdict changed on {replay.thesis_recheck.changed} of {replay.thesis_recheck.rechecked} rechecked theses</p> : null}
      {settings?.overrides?.length ? <p className="text-warning">Overrides production: {formatTokenList(settings.overrides)}</p> : null}
      {settings?.selector_settings_differing_from_local?.length ? <p className="text-dim">Selector settings differing from local: {settings.selector_settings_differing_from_local.join(', ')}</p> : null}
      {missingBars.length ? <p className="text-warning">No daily bars for {missingBars.join(', ')}</p> : null}
    </div>
  </Panel>
}

// ---- Tables -----------------------------------------------------------------

const EVENT_COLUMNS: Column<BacktestEvent>[] = [
  { key: 'as-of', header: 'As of', render: (event) => <><TickerChip symbol={event.symbol} /><span className="block whitespace-nowrap text-[11px] text-dim">{formatShortDateTime(event.as_of, { placeholder: EMPTY })}</span></> },
  { key: 'decision', header: 'Decision', render: (event) => formatToken(event.decision) },
  { key: 'contract', header: 'Contract', render: (event) => <><span className="font-mono">{event.option_symbol || EMPTY}</span><span className="block text-[11px] text-dim">{event.quantity ?? 0} contracts</span></> },
  { key: 'status', header: 'Status', render: (event) => formatToken(event.status) },
  { key: 'source', header: 'Source', render: (event) => <>{event.pricing_source || EMPTY}<span className="block text-[11px] text-dim">{event.quote_provider || ''}</span></> },
  { key: 'evidence', header: 'Evidence', render: (event) => `Snapshot #${event.evidence_snapshot_id ?? EMPTY}` },
  { key: 'prices', header: 'Entry → exit', align: 'right', render: (event) => (event.entry_price === null ? EMPTY : `${event.entry_price.toFixed(2)} → ${event.exit_price?.toFixed(2) ?? EMPTY}`) },
  { key: 'pnl', header: 'P/L', align: 'right', render: (event) => <span className={pnlClass(event.realized_pnl_pct)}>{percent(event.realized_pnl_pct)}</span> },
  { key: 'warnings', header: 'Warnings', render: (event) => formatTokenList(event.warnings) || EMPTY },
]

function QuoteFetchResult({ result }: { result: EodQuoteFetch }) {
  const range = (start?: string | null, end?: string | null, none = 'none') => (start && end ? `${when(start)} → ${when(end)}` : none)
  return <Panel title={`EOD quote fetch #${result.fetch_run_id}`} meta={`${result.provider.replaceAll('_', ' ')} · ${result.status.replaceAll('_', ' ')} · no trades are executed`}>
    <div className="flex flex-col gap-1 text-xs text-ink-2">
      <p>Imported {result.inserted_quote_count} quotes · {result.already_cached_count} decisions already cached · {result.no_data_count + result.unavailable_count + result.error_count} gaps · {result.remaining_decision_count ?? 0} windows pending</p>
      <p>Latest decision with a recorded contract: {result.latest_contract_decision_at ? when(result.latest_contract_decision_at) : 'none'} · selected decision dates: {range(result.selected_start_at, result.selected_end_at, 'no windows selected')} · quotes returned: {range(result.returned_quote_start_at, result.returned_quote_end_at)}</p>
      {result.stop_reason ? <p role="status" className="text-warning">Paused: {result.stop_reason.replaceAll('_', ' ')}.{result.resume_at ? ` Try again after ${when(result.resume_at)}.` : ''} Quotes already imported are available for backtesting.</p> : <p>Fetch the next batch to continue, then create a new backtest.</p>}
      {result.budget ? <p className="text-dim">Daily fetch allowance: {result.budget.used} / {result.budget.daily_limit} used (includes failed attempts). Resets {when(result.budget.resets_at)}.</p> : null}
    </div>
  </Panel>
}

function RunDetail({ detail, runs }: { detail: Detail; runs: readonly Run[] }) {
  const { run, events } = detail
  const metrics = run.metrics
  const filled = metrics.filled_count ?? 0
  const gaps = dataGaps(events, hasNumericValue(metrics.expectancy_pct))
  const examined = metrics.snapshot_count ?? 0
  const decisionStart = metrics.decision_start_at ?? events[0]?.as_of
  const decisionEnd = metrics.decision_end_at ?? events[events.length - 1]?.as_of
  const beforeLimits = metrics.before_portfolio_limits
  return <div className="flex min-w-0 flex-col gap-3">
    <div className="flex flex-wrap items-center gap-2">
      <h2 className="text-sm font-semibold">Run #{run.id} · {run.strategy_version}</h2>
      <Tag tone={run.status === 'completed' ? 'neutral' : 'loss'}>{formatToken(run.status)}</Tag>
      <span className="text-[11px] text-dim">
        {modelName(run)} · {run.prompt_version || 'frozen input'} · decisions {decisionStart && decisionEnd ? `${formatDate(decisionStart)} – ${formatDate(decisionEnd)}` : 'none matched'}
        {metrics.available_decision_count !== undefined ? ` · included ${metrics.decision_count ?? events.length} of ${metrics.available_decision_count}` : ''}
        {(metrics.omitted_older_decision_count ?? 0) > 0 ? ` · ${metrics.omitted_older_decision_count} older excluded by the limit` : ''}
      </span>
      {run.error_message ? <p role="alert" className="basis-full text-xs text-error">{run.error_message}</p> : null}
    </div>
    <StatStrip items={[
      { label: 'Filled', value: filled, sub: `${percent(metrics.fill_rate_pct)} fill rate` },
      { label: 'Expectancy', value: percent(metrics.expectancy_pct), sub: `${percent(metrics.win_rate_pct)} win rate${hasNumericValue(metrics.median_pnl_pct) ? ` · median ${percent(metrics.median_pnl_pct)}` : ''} · n=${filled}`, tone: expectancyTone(metrics.expectancy_pct, filled) },
      { label: 'Total P/L', value: dollars(metrics.total_pnl_dollars), sub: `commissions ${dollars(metrics.total_commission_dollars)}${hasNumericValue(metrics.profit_factor_like) ? ` · profit factor ${metrics.profit_factor_like.toFixed(2)}` : ''}`, tone: (metrics.total_pnl_dollars ?? 0) < 0 ? 'loss' : 'gain' },
      { label: 'Max drawdown', value: dollars(metrics.max_drawdown_dollars), sub: 'simulated', tone: metrics.max_drawdown_dollars ? 'loss' : undefined },
      { label: 'Examined', value: examined, sub: <>{metrics.no_fill_count ?? 0} no fill · {metrics.portfolio_rejected_count ?? 0} rejects{beforeLimits ? <span className="block">before limits: {beforeLimits.priced_entry_count} priced · {percent(beforeLimits.expectancy_pct)}</span> : null}</> },
    ]} />
    {gaps.length ? <div>
      <p className="text-xs text-warning">{gaps.join(' · ')}</p>
      <p className="mt-0.5 text-[11px] text-dim">Expectancy averages closed simulations only. Simulation and quote-fetch limits are separate, and saved runs do not update when quotes arrive.</p>
    </div> : null}
    <div className="flex flex-wrap gap-3">
      <Panel className="min-w-72 flex-[2_1_24rem]" title="Simulated cumulative P/L">
        {metrics.equity_curve?.length ? <EquityCurve points={metrics.equity_curve} /> : <p className="text-dim">No closed simulated fills yet.</p>}
      </Panel>
      <Panel className="min-w-64 flex-[1_1_16rem]" title={`Where the ${events.length} decisions went`}>
        {events.length ? <OutcomeBars events={events} /> : <p className="text-dim">No call/put decisions in this run.</p>}
      </Panel>
    </div>
    {metrics.replay ? <ReplaySummaryPanel replay={metrics.replay} /> : null}
    <RunSettings run={run} />
    <Panel title="Expectancy by run" meta="selected run outlined">
      <ExpectancyByRun runs={runs} selectedId={run.id} />
    </Panel>
    <Panel title="Event ledger" meta={`slippage ${dollars(metrics.total_slippage_dollars)}`} flush>
      <DataTable label="Event ledger" columns={EVENT_COLUMNS} rows={events} rowKey={(event) => String(event.id)} emptyMessage="No manager call/put decisions matched this run." />
    </Panel>
  </div>
}

// ---- Page -------------------------------------------------------------------

function BacktestPage() {
  const [runs, setRuns] = useState<Run[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [fetchingQuotes, setFetchingQuotes] = useState(false)
  const [quoteFetch, setQuoteFetch] = useState<EodQuoteFetch | null>(null)
  const [windowMode, setWindowMode] = useState<'all' | 'latest' | 'range'>('all')
  const [rangeStart, setRangeStart] = useState('')
  const [rangeEnd, setRangeEnd] = useState('')
  const [kind, setKind] = useState<RunKind | 'all'>('all')
  const [model, setModel] = useState('all')
  const [status, setStatus] = useState('all')
  const [sortKey, setSortKey] = useState<RunSortKey>('id')
  const [sortAscending, setSortAscending] = useState(false)
  const [decisionLimit, setDecisionLimit] = useState(250)
  const [fetchLimit, setFetchLimit] = useState(10)
  const [config, setConfig] = useState(DEFAULT_CONFIG)

  const loadRuns = () => getRuns().then((nextRuns) => { setRuns(nextRuns); return nextRuns })
  useEffect(() => {
    let active = true
    loadRuns()
      .then((nextRuns) => { if (active && nextRuns[0]) return getRun(nextRuns[0].id).then((next) => { if (active) setDetail(next) }) })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Failed to load backtests.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])
  const selectRun = (runId: number) => {
    setLoading(true)
    getRun(runId)
      .then((next) => { setDetail(next); setError(null) })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Failed to load backtest.'))
      .finally(() => setLoading(false))
  }
  const startRun = () => {
    setRunning(true)
    const range = { start_at: dayStart(rangeStart), end_at: dayEnd(rangeEnd) }
    const window: DecisionWindow = windowMode === 'range' ? { limit: null, ...range } : { limit: windowMode === 'all' ? null : decisionLimit }
    createRun(config, window)
      .then((next) => { setDetail(next); return loadRuns() })
      .then(() => setError(null))
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Failed to run backtest.'))
      .finally(() => setRunning(false))
  }
  const startEodFetch = () => {
    setFetchingQuotes(true)
    fetchEodQuotes(fetchLimit, config.max_hold_hours, windowMode === 'range' ? { start_at: dayStart(rangeStart), end_at: dayEnd(rangeEnd) } : {})
      .then((result) => { setQuoteFetch(result); setError(null) })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Failed to fetch EOD quotes.'))
      .finally(() => setFetchingQuotes(false))
  }

  const models = useMemo(() => [...new Set(runs.map(modelName))].sort(), [runs])
  const statuses = useMemo(() => [...new Set(runs.map((run) => run.status))].sort(), [runs])
  const filteredRuns = useMemo(() => runs.filter((run) => (kind === 'all' || runKind(run) === kind)
    && (model === 'all' || modelName(run) === model) && (status === 'all' || run.status === status)), [runs, kind, model, status])
  const visibleRuns = useMemo(() => sortRuns(filteredRuns, sortKey, sortAscending), [filteredRuns, sortKey, sortAscending])
  const toggleSort = (nextKey: RunSortKey) => {
    if (sortKey === nextKey) setSortAscending((current) => !current)
    else { setSortKey(nextKey); setSortAscending(false) }
  }
  const sortHeader = (key: RunSortKey, text: string) => <button type="button" className="hover:text-primary" onClick={() => toggleSort(key)}>{text}{sortKey === key ? (sortAscending ? ' ↑' : ' ↓') : ''}</button>
  const filterSelect = (label: string, value: string, onChange: (value: string) => void, options: [string, string][]) => <label className="flex flex-col gap-1">
    <span className="text-[11px] text-dim">{label}</span>
    <select className="select select-xs max-w-40" value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="all">All</option>
      {options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}
    </select>
  </label>

  const runColumns: Column<Run>[] = [
    { key: 'run', header: sortHeader('id', 'Run'), render: (run) => <>
      <button type="button" className="link font-mono font-semibold no-underline" aria-pressed={detail?.run.id === run.id} onClick={() => selectRun(run.id)}>#{run.id}</button>
      <span className="block whitespace-nowrap text-[11px] text-dim">{formatShortDateTime(run.started_at, { placeholder: 'Unknown' })}</span>
    </> },
    { key: 'model', header: 'Model', render: (run) => <span className="block max-w-44 truncate" title={modelName(run)}>{modelName(run)}{run.status !== 'completed' ? <span className="block text-[11px] text-error">{formatToken(run.status)}</span> : null}</span> },
    { key: 'filled', header: sortHeader('filled_count', 'n'), align: 'right', render: (run) => <span className="font-mono">{run.metrics.filled_count ?? 0}</span> },
    { key: 'expectancy', header: <>{sortHeader('expectancy_pct', 'Expectancy')}<span className="block">{sortHeader('total_pnl_dollars', 'P/L')}</span></>, align: 'right', render: (run) => <>
      <span className={`block font-mono ${pnlClass(run.metrics.expectancy_pct)}`}>{percent(run.metrics.expectancy_pct)}</span>
      <span className={`block font-mono text-[11px] ${pnlClass(run.metrics.total_pnl_dollars)}`}>{formatSignedCurrency(run.metrics.total_pnl_dollars ?? 0)}</span>
    </> },
  ]

  return <main className="flex flex-col gap-3">
    <PageHeader
      title="Historical decision backtest"
      description="Replays stored decisions against historical option quotes. No live data, orders or account state."
      actions={<>
        <button className="btn btn-sm" type="button" onClick={startEodFetch} disabled={fetchingQuotes}>{fetchingQuotes ? 'Fetching EOD quotes…' : 'Fetch next EOD batch'}</button>
        <button className="btn btn-sm btn-primary" type="button" onClick={startRun} disabled={running}>{running ? 'Running…' : 'Run backtest'}</button>
      </>}
    />
    <div role="group" aria-label="Backtest settings" className="flex flex-wrap gap-3">
      <Fieldset legend="Run scope">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-dim">Decision window</span>
          <select className="select select-xs" value={windowMode} onChange={(event) => setWindowMode(event.target.value as typeof windowMode)}>
            <option value="all">All history</option>
            <option value="latest">Latest decisions</option>
            <option value="range">Date range</option>
          </select>
        </label>
        {windowMode === 'latest' ? <NumberField label="Latest decisions" min={1} max={1000} value={decisionLimit} onChange={(value) => setDecisionLimit(value ?? decisionLimit)} /> : null}
        {windowMode === 'range' ? <>
          <label className="flex flex-col gap-1"><span className="text-[11px] text-dim">From</span><input type="date" className="input input-xs font-mono" max={rangeEnd || undefined} value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} /></label>
          <label className="flex flex-col gap-1"><span className="text-[11px] text-dim">To</span><input type="date" className="input input-xs font-mono" min={rangeStart || undefined} value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} /></label>
        </> : null}
        <NumberField label="EOD batch size" min={1} max={100} value={fetchLimit} onChange={(value) => setFetchLimit(value ?? fetchLimit)} />
      </Fieldset>
      {FIELD_GROUPS.map((group) => <Fieldset key={group.legend} legend={group.legend}>
        {group.fields.map((field) => <NumberField key={field.key} label={field.label} min={field.min} max={field.max} step={field.step} blank={field.blank} value={config[field.key]} onChange={(value) => setConfig((current) => ({ ...current, [field.key]: value }))} />)}
      </Fieldset>)}
    </div>
    <details className="text-xs text-ink-2">
      <summary className="cursor-pointer text-[11px] text-dim">How runs and quote fetches work</summary>
      <div className="mt-1 flex max-w-[80ch] flex-col gap-1">
        <p>All history includes every stored call/put decision, simulated oldest to newest. Latest decisions limits the run to a recent window; Date range to the decisions made between those days (either end may be left open). A date range also bounds the EOD fetch.</p>
        <p>The form runs stored-decision backtests only. LLM replay runs come from <code>agentCallers/llm_backtest.py</code>; their replay summary and recorded settings show under the selected run.</p>
        <p>Each EOD fetch advances through uncached contract windows across history; completed cached windows do not consume the batch limit. MarketData.app EOD quotes are fetched server-side; the token never reaches your browser.</p>
        <p>Portfolio limits reject new simulated entries once capacity, entry-notional, sector, or realized daily-loss limits are reached. The sector cap is measured against the portfolio-notional cap.</p>
      </div>
    </details>
    {quoteFetch ? <QuoteFetchResult result={quoteFetch} /> : null}
    {error ? <p role="alert" className="text-xs text-error">{error}</p> : null}
    {loading && !detail ? <p className="text-dim">Loading backtest ledger…</p> : null}
    <div className="grid gap-3 lg:grid-cols-[minmax(16rem,22rem)_minmax(0,1fr)] lg:items-start">
      <Panel title="Run history" meta={`${filteredRuns.length} of ${runs.length} · immutable`} flush>
        <div role="group" aria-label="Run filters" className="flex flex-wrap gap-2 border-b border-base-300 px-3.5 py-2">
          {filterSelect('Kind', kind, (value) => setKind(value as RunKind | 'all'), [['stored', 'Stored'], ['llm', 'LLM replay']])}
          {filterSelect('Model', model, setModel, models.map((name) => [name, name]))}
          {filterSelect('Status', status, setStatus, statuses.map((name) => [name, formatToken(name)]))}
        </div>
        <DataTable label="Run history" columns={runColumns} rows={visibleRuns} rowKey={(run) => String(run.id)} rowClassName={(run) => (detail?.run.id === run.id ? 'bg-raised' : '')} emptyMessage={runs.length ? 'No runs match these filters.' : 'No backtests have been run yet.'} />
      </Panel>
      {detail ? <RunDetail detail={detail} runs={filteredRuns} /> : null}
    </div>
  </main>
}

export default BacktestPage
