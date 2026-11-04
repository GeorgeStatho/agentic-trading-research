import { useMemo, useState } from 'react'

import { getExpectancy, pooledStats, sortCohorts } from './expectancy'
import type { Basis, Cohort, ExpectancyPayload, GroupBy, SortKey } from './expectancy'
import { formatDate, formatDecimal, formatPercent, formatSignedPercent } from './format'
import PageHeader from './pageHeader'
import type { Column } from './ui/DataTable'
import { DataTable } from './ui/DataTable'
import { Meter } from './ui/Meter'
import { Panel } from './ui/Panel'
import { Select } from './ui/Select'
import { StatStrip } from './ui/StatStrip'
import type { Stat } from './ui/StatStrip'
import { useFetchedResource } from './useRemoteResource'

/** This page prints an em dash rather than "N/A" for cohorts with no samples. */
const NO_SAMPLES = '—'

const BASIS_OPTIONS = [{ value: 'marked', label: 'Marked P/L' }, { value: 'closed', label: 'Closed realized P/L' }] as const
const GROUP_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: 'confidence', label: 'Confidence' },
  { value: 'dte_bucket', label: 'DTE bucket' },
  { value: 'selector_mode', label: 'Selector mode' },
  { value: 'sector', label: 'Sector' },
  { value: 'time_of_day', label: 'Time of day' },
  { value: 'entry_gate', label: 'Continuation gate' },
  { value: 'market_regime', label: 'Market regime' },
  { value: 'evidence_count_bucket', label: 'Evidence count' },
  { value: 'trend_state', label: 'Trend state' },
  { value: 'relative_strength_state', label: 'Relative strength' },
  { value: 'volume_state', label: 'Volume state' },
  { value: 'confidence_x_dte_bucket', label: 'Confidence × DTE' },
  { value: 'confidence_x_selector_mode', label: 'Confidence × selector' },
]
const WINDOW_OPTIONS = ['30', '90', '180', '365', '730'].map((value) => ({ value, label: `${value} days` }))
const MIN_SAMPLE_OPTIONS = ['3', '5', '10'].map((value) => ({ value, label: value }))
const DECISION_OPTIONS = [{ value: 'all', label: 'All' }, { value: 'call', label: 'Calls' }, { value: 'put', label: 'Puts' }]
const CONFIDENCE_OPTIONS = [{ value: 'all', label: 'All' }, { value: 'high', label: 'High' }, { value: 'medium', label: 'Medium' }, { value: 'low', label: 'Low' }]

function percent(value: number | null): string {
  return formatSignedPercent(value, { placeholder: NO_SAMPLES })
}

function pnlClass(value: number | null): string {
  if (value === null || value === 0) return ''
  return value > 0 ? 'text-gain' : 'text-loss'
}

function stats(payload: ExpectancyPayload): Stat[] {
  const pooled = pooledStats(payload)
  const { summary, filters } = payload
  const small = pooled.samples < filters.min_samples
  let expectancyTone: Stat['tone'] = 'muted'
  if (!small && pooled.expectancyPct !== null) expectancyTone = pooled.expectancyPct < 0 ? 'loss' : 'gain'
  return [
    { label: 'Eligible trades', value: summary.eligible_trade_count, sub: `last ${filters.within_days} days` },
    { label: 'Resolved samples', value: summary.pnl_sample_count, sub: payload.basis === 'marked' ? 'have a marked P/L' : 'closed' },
    { label: 'Unresolved', value: summary.unresolved_count },
    { label: 'Expectancy', value: percent(pooled.expectancyPct), sub: `n=${pooled.samples} · per trade, all cohorts`, tone: expectancyTone },
    { label: 'Win rate', value: `${pooled.wins} / ${pooled.samples}`, sub: 'resolved only', tone: small ? 'muted' : undefined },
    { label: 'Profit factor', value: formatDecimal(pooled.profitFactor, { placeholder: NO_SAMPLES }), sub: `n=${pooled.samples}`, tone: small ? 'muted' : undefined },
    { label: 'Basis', value: payload.basis === 'marked' ? 'Marked' : 'Closed', sub: payload.basis === 'marked' ? 'not realized' : 'realized' },
  ]
}

function warningLine(payload: ExpectancyPayload): string {
  const parts = payload.basis === 'marked' ? ['Marked P/L, not realized', 'samples expire when their freshness window ends'] : []
  const below = payload.cohorts.filter((cohort) => cohort.insufficient_sample).length
  if (below) parts.push(`${below} of ${payload.cohorts.length} cohorts are below the ${payload.filters.min_samples}-sample guardrail`)
  return parts.join(' · ')
}

function cohortSummary(cohort: Cohort): string {
  return cohort.expectancy_pct === null ? `${cohort.label} no samples yet` : `${cohort.label} ${percent(cohort.expectancy_pct)} (n=${cohort.pnl_sample_count})`
}

/** Diverging bars around zero, one per cohort; a dashed box where nothing has resolved. */
function ExpectancyBars({ cohorts }: { cohorts: readonly Cohort[] }) {
  const scale = Math.max(...cohorts.map((cohort) => Math.abs(cohort.expectancy_pct ?? 0)), 1)
  return (
    <div role="img" aria-label={`Expectancy by cohort: ${cohorts.map(cohortSummary).join(', ')}`} className="flex flex-col gap-2">
      {cohorts.map((cohort) => {
        const value = cohort.expectancy_pct
        const width = `${(Math.abs(value ?? 0) / scale) * 50}%`
        return (
          <div key={cohort.key} className={`grid grid-cols-[minmax(4.5rem,9rem)_1fr_auto] items-center gap-2 text-[11px] ${cohort.insufficient_sample ? 'opacity-60' : ''}`}>
            <span className="truncate text-ink-2">{cohort.label} <span className="text-dim">n={cohort.pnl_sample_count}</span></span>
            <div className="relative h-4">
              <div className="absolute inset-y-0 left-1/2 w-px bg-base-300" />
              {value === null ? (
                <div className="absolute inset-y-0 left-[30%] right-[30%] rounded-sm border border-dashed border-dim" />
              ) : (
                <div
                  className={`absolute inset-y-0.5 rounded-sm ${value < 0 ? 'right-1/2 bg-loss' : 'left-1/2 bg-gain'}`}
                  style={{ width }}
                />
              )}
            </div>
            <span className={`whitespace-nowrap text-right ${pnlClass(value)}`}>{value === null ? <span className="text-dim">{cohort.unresolved_count} unresolved</span> : percent(value)}</span>
          </div>
        )
      })}
    </div>
  )
}

function ResolutionBar({ summary }: { summary: ExpectancyPayload['summary'] }) {
  const total = summary.eligible_trade_count
  const resolved = total ? (summary.pnl_sample_count / total) * 100 : 0
  return (
    <div className="flex flex-col gap-2">
      <div role="img" aria-label={`${summary.pnl_sample_count} of ${total} eligible trades resolved, ${summary.unresolved_count} unresolved`} className="flex h-4 overflow-hidden rounded-sm bg-raised">
        <div className="bg-info" style={{ width: `${resolved}%` }} />
      </div>
      <div className="flex flex-wrap justify-between gap-2 text-[11px] text-dim">
        <span><span className="mr-1 inline-block size-2 rounded-sm bg-info" />{summary.pnl_sample_count} resolved</span>
        <span><span className="mr-1 inline-block size-2 rounded-sm border border-dim" />{summary.unresolved_count} unresolved</span>
      </div>
    </div>
  )
}

function DecisionExpectancyPage() {
  const [basis, setBasis] = useState<Basis>('marked')
  const [groupBy, setGroupBy] = useState<GroupBy>('confidence')
  const [withinDays, setWithinDays] = useState(90)
  const [minSamples, setMinSamples] = useState(5)
  const [decision, setDecision] = useState('all')
  const [confidence, setConfidence] = useState('all')
  const [sector, setSector] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('expectancy_pct')
  const [sortAscending, setSortAscending] = useState(false)
  // A new loader per filter set re-reads the cohorts; the hook keeps the last payload until it arrives.
  const load = useMemo(
    () => () => getExpectancy({ basis, groupBy, withinDays, minSamples, decision, confidence, sector }),
    [basis, groupBy, withinDays, minSamples, decision, confidence, sector],
  )
  const { payload, error, isLoading } = useFetchedResource(load, { errorMessage: 'Failed to load decision expectancy.' })
  const cohorts = useMemo(() => sortCohorts(payload?.cohorts ?? [], sortKey, sortAscending), [payload, sortKey, sortAscending])

  function toggleSort(nextKey: SortKey) {
    if (sortKey === nextKey) {
      setSortAscending((current) => !current)
    } else {
      setSortKey(nextKey)
      setSortAscending(nextKey === 'label')
    }
  }

  function sortHeader(key: SortKey, text: string) {
    const arrow = sortKey === key ? (sortAscending ? ' ↑' : ' ↓') : ''
    return <button type="button" className="hover:text-primary" onClick={() => toggleSort(key)}>{text}{arrow}</button>
  }

  const columns: Column<Cohort>[] = [
    { key: 'label', header: sortHeader('label', 'Cohort'), render: (cohort) => (
      <>
        <span className="font-medium">{cohort.label}</span>
        {cohort.insufficient_sample ? <span className="block text-[11px] text-dim">below sample guardrail</span> : null}
      </>
    ) },
    { key: 'trades', header: sortHeader('trade_count', 'Trades'), align: 'right', render: (cohort) => (
      <>{cohort.trade_count}{cohort.unresolved_count ? <span className="block text-[11px] text-dim">{cohort.unresolved_count} unresolved</span> : null}</>
    ) },
    { key: 'samples', header: sortHeader('pnl_sample_count', 'Samples'), align: 'right', render: (cohort) => cohort.pnl_sample_count },
    { key: 'win', header: sortHeader('win_rate_pct', 'Win rate'), align: 'right', render: (cohort) => formatPercent(cohort.win_rate_pct, { digits: 1, placeholder: NO_SAMPLES }) },
    { key: 'expectancy', header: sortHeader('expectancy_pct', 'Expectancy'), align: 'right', render: (cohort) => <span className={pnlClass(cohort.expectancy_pct)}>{percent(cohort.expectancy_pct)}</span> },
    { key: 'median', header: 'Median', align: 'right', render: (cohort) => percent(cohort.median_pnl_pct) },
    { key: 'avg-win', header: 'Avg win', align: 'right', render: (cohort) => percent(cohort.avg_win_pct) },
    { key: 'avg-loss', header: 'Avg loss', align: 'right', render: (cohort) => percent(cohort.avg_loss_pct) },
    { key: 'pf', header: 'Profit factor', align: 'right', render: (cohort) => formatDecimal(cohort.profit_factor_like, { placeholder: NO_SAMPLES }) },
    { key: 'latest', header: 'Latest', render: (cohort) => formatDate(cohort.latest_decision_at, { placeholder: 'Unknown' }) },
  ]

  const filters = (
    <div role="group" aria-label="Decision expectancy filters" className="flex flex-wrap items-end gap-2">
      <Select label="Basis" options={BASIS_OPTIONS} value={basis} onChange={setBasis} />
      <Select label="Group by" options={GROUP_OPTIONS} value={groupBy} onChange={setGroupBy} />
      <Select label="Window" options={WINDOW_OPTIONS} value={String(withinDays)} onChange={(value) => setWithinDays(Number(value))} />
      <Select label="Min samples" options={MIN_SAMPLE_OPTIONS} value={String(minSamples)} onChange={(value) => setMinSamples(Number(value))} />
      <Select label="Decision" options={DECISION_OPTIONS} value={decision} onChange={setDecision} />
      <Select label="Confidence" options={CONFIDENCE_OPTIONS} value={confidence} onChange={setConfidence} />
      <label className="flex flex-col gap-1">
        <span className="text-[11px] text-dim">Sector</span>
        <input className="input input-sm w-32" value={sector} onChange={(event) => setSector(event.target.value)} placeholder="Any" />
      </label>
    </div>
  )
  const warning = payload ? warningLine(payload) : ''
  const groupLabel = GROUP_OPTIONS.find((option) => option.value === groupBy)?.label.toLowerCase() ?? 'cohort'

  return (
    <main className="flex flex-col gap-3">
      <PageHeader
        title="Decision expectancy"
        description="Average P/L of executed decisions, grouped into cohorts. Read-only; never submits an order."
        meta={payload ? `As of ${formatDate(payload.as_of, { placeholder: 'Unknown' })}` : null}
        actions={filters}
      />
      {error ? <p className="text-xs text-error">{error}</p> : null}
      {isLoading && !payload ? <p className="text-dim">Loading outcome cohorts…</p> : null}
      {payload ? (
        <>
          <StatStrip items={stats(payload)} />
          {warning ? <p className="text-xs text-warning">{warning}</p> : null}
          <div className="flex flex-wrap gap-3">
            <Panel className="min-w-72 flex-[2_1_28rem]" title={`Expectancy by ${groupLabel}`} meta="bar = average P/L · n = resolved samples">
              {payload.cohorts.length ? <ExpectancyBars cohorts={payload.cohorts} /> : <p className="text-dim">No cohorts.</p>}
            </Panel>
            <Panel className="min-w-64 flex-[1_1_16rem]" title={`Samples toward guardrail (${payload.filters.min_samples})`}>
              <div className="flex flex-col gap-3">
                {payload.cohorts.map((cohort) => (
                  <Meter
                    key={cohort.key}
                    label={cohort.label}
                    value={Math.min(cohort.pnl_sample_count, payload.filters.min_samples)}
                    max={payload.filters.min_samples}
                    tone={cohort.insufficient_sample ? 'primary' : 'success'}
                  />
                ))}
              </div>
            </Panel>
            <Panel className="min-w-64 flex-[1_1_16rem]" title={`Resolution of ${payload.summary.eligible_trade_count} eligible trades`}>
              <ResolutionBar summary={payload.summary} />
            </Panel>
          </div>
          <Panel title="Cohorts" meta={payload.basis_label} flush>
            <DataTable
              columns={columns}
              rows={cohorts}
              rowKey={(cohort) => cohort.key}
              rowClassName={(cohort) => (cohort.insufficient_sample ? 'opacity-60' : '')}
              emptyMessage="No executed decisions match these filters."
            />
          </Panel>
        </>
      ) : null}
    </main>
  )
}

export default DecisionExpectancyPage
