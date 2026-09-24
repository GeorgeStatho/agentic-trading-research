import { useEffect, useMemo, useState } from 'react'

import { formatDate, formatDecimal, formatSignedPercent } from './format'
import './decisionExpectancy.css'

/** This page prints an em dash rather than "N/A" for cohorts with no samples. */
const NO_SAMPLES_PLACEHOLDER = '—'

type Basis = 'marked' | 'closed'
type GroupBy = 'confidence' | 'dte_bucket' | 'selector_mode' | 'sector' | 'time_of_day' | 'confidence_x_dte_bucket' | 'confidence_x_selector_mode' | 'entry_gate' | 'market_regime' | 'evidence_count_bucket' | 'trend_state' | 'relative_strength_state' | 'volume_state'
type SortKey = 'label' | 'trade_count' | 'pnl_sample_count' | 'win_rate_pct' | 'expectancy_pct'

type Cohort = {
  key: string
  label: string
  trade_count: number
  pnl_sample_count: number
  unresolved_count: number
  win_count: number
  loss_count: number
  flat_count: number
  win_rate_pct: number | null
  avg_pnl_pct: number | null
  median_pnl_pct: number | null
  avg_win_pct: number | null
  avg_loss_pct: number | null
  expectancy_pct: number | null
  profit_factor_like: number | null
  insufficient_sample: boolean
  latest_decision_at: string
}

type Payload = {
  as_of: string
  basis: Basis
  basis_label: string
  filters: { group_by: GroupBy; within_days: number; min_samples: number; decision: string; confidence: string; sector: string | null }
  summary: { eligible_trade_count: number; pnl_sample_count: number; unresolved_count: number }
  cohorts: Cohort[]
  read_only: boolean
}

function formatCohortPercent(value: number | null): string {
  return formatSignedPercent(value, { placeholder: NO_SAMPLES_PLACEHOLDER })
}

function formatCohortDate(value: string): string {
  return formatDate(value, { placeholder: 'Unknown' })
}

async function getExpectancy(filters: { basis: Basis; groupBy: GroupBy; withinDays: number; minSamples: number; decision: string; confidence: string; sector: string }): Promise<Payload> {
  const params = new URLSearchParams({
    basis: filters.basis,
    group_by: filters.groupBy,
    within_days: String(filters.withinDays),
    min_samples: String(filters.minSamples),
    decision: filters.decision,
    confidence: filters.confidence,
  })
  if (filters.sector.trim()) params.set('sector', filters.sector.trim().toLowerCase())
  const response = await fetch(`/api/decision-expectancy?${params.toString()}`)
  const body = await response.json().catch(() => null) as Payload | { error?: string } | null
  if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to load expectancy: ${response.status}`)
  return body as Payload
}

function DecisionExpectancyPage() {
  const [basis, setBasis] = useState<Basis>('marked')
  const [groupBy, setGroupBy] = useState<GroupBy>('confidence')
  const [withinDays, setWithinDays] = useState(90)
  const [minSamples, setMinSamples] = useState(5)
  const [decision, setDecision] = useState('all')
  const [confidence, setConfidence] = useState('all')
  const [sector, setSector] = useState('')
  const [payload, setPayload] = useState<Payload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState<SortKey>('expectancy_pct')
  const [sortAscending, setSortAscending] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    getExpectancy({ basis, groupBy, withinDays, minSamples, decision, confidence, sector })
      .then((nextPayload) => {
        if (!active) return
        setPayload(nextPayload)
        setError(null)
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Failed to load decision expectancy.')
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [basis, groupBy, withinDays, minSamples, decision, confidence, sector])

  const cohorts = useMemo(() => [...(payload?.cohorts ?? [])].sort((left, right) => {
    const leftValue = left[sortKey]
    const rightValue = right[sortKey]
    const comparison = typeof leftValue === 'string' || typeof rightValue === 'string'
      ? String(leftValue ?? '').localeCompare(String(rightValue ?? ''))
      : (Number(leftValue ?? Number.NEGATIVE_INFINITY) - Number(rightValue ?? Number.NEGATIVE_INFINITY))
    return sortAscending ? comparison : -comparison
  }), [payload, sortAscending, sortKey])

  const toggleSort = (nextKey: SortKey) => {
    if (sortKey === nextKey) setSortAscending((current) => !current)
    else {
      setSortKey(nextKey)
      setSortAscending(nextKey === 'label')
    }
  }

  return <main className="decision-expectancy">
    <section className="decision-expectancy__hero">
      <div><p>Decision Expectancy</p><h1>Deterministic Outcome Cohorts</h1><span>Read-only analytics from manager decisions and stored outcomes. This feature never submits an order.</span></div>
      {payload ? <small>As of {formatCohortDate(payload.as_of)}</small> : null}
    </section>

    <section className="decision-expectancy__controls" aria-label="Decision expectancy filters">
      <label>Basis<select value={basis} onChange={(event) => setBasis(event.target.value as Basis)}><option value="marked">Marked P/L</option><option value="closed">Closed realized P/L</option></select></label>
      <label>Group by<select value={groupBy} onChange={(event) => setGroupBy(event.target.value as GroupBy)}><option value="confidence">Confidence</option><option value="dte_bucket">DTE bucket</option><option value="selector_mode">Selector mode</option><option value="sector">Sector</option><option value="time_of_day">Time of day</option><option value="entry_gate">Continuation gate</option><option value="market_regime">Market regime</option><option value="evidence_count_bucket">Evidence count</option><option value="trend_state">Trend state</option><option value="relative_strength_state">Relative strength</option><option value="volume_state">Volume state</option><option value="confidence_x_dte_bucket">Confidence × DTE</option><option value="confidence_x_selector_mode">Confidence × selector</option></select></label>
      <label>Window<select value={withinDays} onChange={(event) => setWithinDays(Number(event.target.value))}><option value={30}>30 days</option><option value={90}>90 days</option><option value={180}>180 days</option><option value={365}>365 days</option><option value={730}>730 days</option></select></label>
      <label>Min samples<select value={minSamples} onChange={(event) => setMinSamples(Number(event.target.value))}><option value={3}>3</option><option value={5}>5</option><option value={10}>10</option></select></label>
      <label>Decision<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="all">All</option><option value="call">Calls</option><option value="put">Puts</option></select></label>
      <label>Confidence<select value={confidence} onChange={(event) => setConfidence(event.target.value)}><option value="all">All</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
      <label>Sector<input value={sector} onChange={(event) => setSector(event.target.value)} placeholder="technology" /></label>
    </section>

    {basis === 'marked' ? <section className="decision-expectancy__notice">Marked P/L, not realized P/L. Samples expire when their stored P/L freshness window ends.</section> : null}
    {error ? <section className="decision-expectancy__empty">{error}</section> : null}
    {loading && !payload ? <section className="decision-expectancy__empty">Loading deterministic outcome cohorts…</section> : null}
    {payload ? <>
      <section className="decision-expectancy__summary">
        <article><span>Eligible trades</span><b>{payload.summary.eligible_trade_count}</b></article>
        <article><span>P/L samples</span><b>{payload.summary.pnl_sample_count}</b></article>
        <article><span>Unresolved</span><b>{payload.summary.unresolved_count}</b></article>
        <article><span>Basis</span><b>{payload.basis === 'marked' ? 'Marked' : 'Closed'}</b></article>
      </section>
      <section className="decision-expectancy__table-card">
        <header><div><p>Expectancy cohorts</p><h2>Average P/L is the canonical expectancy</h2></div><span>{payload.basis_label}</span></header>
        <div className="decision-expectancy__table-wrap"><table><thead><tr>
          <th><button type="button" onClick={() => toggleSort('label')}>Cohort</button></th>
          <th><button type="button" onClick={() => toggleSort('trade_count')}>Trades</button></th>
          <th><button type="button" onClick={() => toggleSort('pnl_sample_count')}>Samples</button></th>
          <th><button type="button" onClick={() => toggleSort('win_rate_pct')}>Win rate</button></th>
          <th><button type="button" onClick={() => toggleSort('expectancy_pct')}>Expectancy</button></th>
          <th>Median</th><th>Avg win</th><th>Avg loss</th><th>Profit factor</th><th>Latest</th>
        </tr></thead><tbody>{cohorts.map((cohort) => <tr key={cohort.key} className={cohort.insufficient_sample ? 'decision-expectancy__muted' : ''}>
          <td><strong>{cohort.label}</strong>{cohort.insufficient_sample ? <small>Below sample guardrail</small> : null}</td>
          <td>{cohort.trade_count}<small>{cohort.unresolved_count} unresolved</small></td><td>{cohort.pnl_sample_count}</td><td>{formatCohortPercent(cohort.win_rate_pct)}</td>
          <td className={cohort.expectancy_pct !== null && cohort.expectancy_pct < 0 ? 'decision-expectancy__negative' : 'decision-expectancy__positive'}>{formatCohortPercent(cohort.expectancy_pct)}</td>
          <td>{formatCohortPercent(cohort.median_pnl_pct)}</td><td>{formatCohortPercent(cohort.avg_win_pct)}</td><td>{formatCohortPercent(cohort.avg_loss_pct)}</td><td>{formatDecimal(cohort.profit_factor_like, { placeholder: NO_SAMPLES_PLACEHOLDER })}</td><td>{formatCohortDate(cohort.latest_decision_at)}</td>
        </tr>)}</tbody></table></div>
        {!cohorts.length ? <p className="decision-expectancy__empty">No executed decisions match these filters.</p> : null}
      </section>
    </> : null}
  </main>
}

export default DecisionExpectancyPage
