import { useEffect, useMemo, useState } from 'react'

import { formatDateTime, formatDteBucket, formatToken, formatTokenList } from './format'
import PageHeader from './pageHeader'
import './decisionAutopsy.css'

type Article = { article_id: number; title: string; source: string; published_at: string; article_scope: string }
type Claim = { claim_type?: string; claim?: string; evidence_article_ids?: number[] }
type ExecutionMode = 'paper' | 'live' | 'unlinked'
type V2Verdict = { stage: string; label: string; reason_code: string; shadow: boolean; occurred_at: string }
type Record = {
  id: number
  symbol: string
  company_name: string
  decision_run_at: string
  strategist: { decision: string; confidence: string; model: string; summary: string; thesis: string[]; risks: string[]; evidence_claims: Claim[] }
  manager: { decision: string; confidence: string; reason: string }
  evidence: Article[]
  constraints: { target_dte_bucket: string; selected_option_id: string; selected_option_symbol: string; selected_expiration_date: string; selected_strike_price: number | null; selected_option_source: string; selection_debug?: { selected_option_snapshot?: { spread_pct?: number | null; open_interest?: number | null; dte?: number | null } }; entry_gate?: { allowed?: boolean; policy_version?: string; reason_codes?: string[]; evidence?: { citation_count?: number; independent_source_count?: number }; technical?: { trend_state?: string; relative_strength_confirmation?: string; volume_confirmation?: string } }; market_regime?: { state?: string; macro_event_window?: { active?: boolean; next_event?: string | null } }; market_regime_gate?: { allowed?: boolean; policy_version?: string; reason_codes?: string[]; regime_state?: string } }
  outcome: { trade_executed: boolean; order_id: string; pnl_pct: number | null; pnl_updated_at: string; resolved_outcome_label: string }
  execution?: { record_id: number | null; mode: ExecutionMode; paper: boolean | null; status: string; option_symbol: string; underlying_symbol: string; quantity: number | null; side: string; submitted_at: string }
  review: { status: string; headline: string; what_proved_us_wrong: string }
  /** Absent from the seeded demo payload, which records no candidate lifecycle. */
  v2_verdicts?: V2Verdict[]
}

type Payload = { as_of: string; count: number; records: Record[]; read_only: boolean; no_trade_executed_by_feature: boolean; v2_verdicts_available?: boolean }
type DemoScenario = { demo_mode: true; disclaimer: string; autopsy: Payload }

function formatDate(value: string): string {
  return formatDateTime(value, { placeholder: 'Unknown time' })
}

function tone(value: string): string {
  const normalized = value.toLowerCase()
  if (normalized.includes('adverse') || normalized === 'put') return 'negative'
  if (normalized.includes('favorable') || normalized === 'call' || normalized.includes('candidate')) return 'positive'
  return 'neutral'
}

function executionModeLabel(mode: ExecutionMode): string {
  if (mode === 'live') return 'Live order'
  if (mode === 'paper') return 'Paper order'
  return 'Execution link unavailable'
}

async function getAutopsies(): Promise<Payload> {
  const response = await fetch('/api/decision-autopsies?limit=100')
  if (!response.ok) throw new Error(`Failed to load ledger: ${response.status}`)
  return response.json() as Promise<Payload>
}

async function getDemoAutopsies(): Promise<Payload> {
  const response = await fetch('/api/demo-mode')
  if (!response.ok) throw new Error(`Failed to load demo mode: ${response.status}`)
  const payload = await response.json() as DemoScenario
  return payload.autopsy
}

/**
 * What the V2 gates made of this candidate.
 *
 * A ledger that cannot be read and a candidate no gate objected to produce the
 * same empty list, so the two are stated apart. A shadow verdict is one the
 * gate recorded without acting on it: the trade went ahead anyway.
 */
export function V2VerdictList({ available, verdicts }: { available: boolean; verdicts?: V2Verdict[] }) {
  return <section className="autopsy-verdicts">
    <p className="autopsy-eyebrow">V2 Gate Verdicts</p>
    <h3>What the V2 gates made of this candidate</h3>
    {!available
      ? <p>V2 verdicts are not available for this ledger, so nothing here says whether a V2 gate objected.</p>
      : verdicts?.length
        ? <ul>{verdicts.map((verdict) => <li key={`${verdict.stage}-${verdict.reason_code}`}>
            <b>{verdict.label}</b>
            <span>{verdict.reason_code.replaceAll('_', ' ')}</span>
            <i className={`autopsy-verdict autopsy-verdict--${verdict.shadow ? 'shadow' : 'enforced'}`}>{verdict.shadow ? 'would have blocked' : 'blocked'}</i>
          </li>)}</ul>
        : <p>No V2 gate recorded a verdict against this candidate.</p>}
  </section>
}

function DecisionAutopsyPage({ demoMode = false }: { demoMode?: boolean }) {
  const [payload, setPayload] = useState<Payload | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const load = demoMode ? getDemoAutopsies() : getAutopsies()
    load.then((next) => {
      if (!active) return
      setPayload(next)
      setSelectedId(next.records[0]?.id ?? null)
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Failed to load ledger.')
    })
    return () => { active = false }
  }, [demoMode])

  const selected = useMemo(() => payload?.records.find((record) => record.id === selectedId) ?? payload?.records[0] ?? null, [payload, selectedId])
  const counts = useMemo(() => ({
    proposed: payload?.records.length ?? 0,
    executed: payload?.records.filter((record) => record.outcome.trade_executed).length ?? 0,
    adverse: payload?.records.filter((record) => record.review.status === 'adverse_outcome').length ?? 0,
  }), [payload])

  return <main className="autopsy-page">
    <PageHeader
      title="Decision autopsy"
      description="Immutable decision-time evidence for every proposal, and what happened next. Read-only."
      meta={`${counts.proposed} proposals · ${counts.executed} executed · ${counts.adverse} adverse outcomes`}
    />
    {demoMode ? <section className="autopsy-demo-notice">Seeded fictional walkthrough. This review creates no trade, order, or live outcome.</section> : null}
    {error ? <section className="autopsy-empty">{error}</section> : null}
    {!error && !payload ? <section className="autopsy-empty">Loading immutable decision records…</section> : null}
    {payload && <section className="autopsy-layout">
      <aside className="autopsy-queue"><p className="autopsy-eyebrow">Decision Queue</p><h2>Proposals</h2>{payload.records.map((record) => <button key={record.id} type="button" onClick={() => setSelectedId(record.id)} className={record.id === selected?.id ? 'autopsy-queue__item autopsy-queue__item--active' : 'autopsy-queue__item'}><span>{record.symbol} · {formatToken(record.manager.decision, { placeholder: 'No trade' })}</span><strong>{record.company_name || 'Stored proposal'}</strong><small>{formatDate(record.decision_run_at)}</small><i className={`autopsy-status autopsy-status--${tone(record.review.status)}`}>{formatToken(record.review.status)}</i></button>)}</aside>
      <section className="autopsy-detail">
        {selected ? <>
          <header className="autopsy-detail__header"><div><p className="autopsy-eyebrow">Immutable Snapshot #{selected.id}</p><h2>{selected.symbol} · {formatToken(selected.manager.decision, { placeholder: 'No trade' })}</h2><span>{selected.company_name} · {formatDate(selected.decision_run_at)}</span></div><div className={`autopsy-decision autopsy-decision--${tone(selected.manager.decision)}`}>{selected.manager.confidence || 'Unrated'} confidence</div></header>
          <div className="autopsy-detail__grid">
            <article><p className="autopsy-eyebrow">Strategist thesis</p><h3>{formatToken(selected.strategist.decision, { placeholder: 'No strategist decision' })}</h3><p>{selected.strategist.summary || 'No strategist summary was preserved.'}</p>{selected.strategist.thesis.length ? <ul>{selected.strategist.thesis.map((item) => <li key={item}>{item}</li>)}</ul> : null}<small>{selected.strategist.model ? `Model ${selected.strategist.model}` : 'Stored strategist output'}</small></article>
            <article><p className="autopsy-eyebrow">Manager decision</p><h3>{formatToken(selected.manager.decision, { placeholder: 'No trade' })}</h3><p>{selected.manager.reason || 'No manager reason was preserved.'}</p><dl><div><dt>Confidence</dt><dd>{selected.manager.confidence || 'N/A'}</dd></div><div><dt>Target DTE</dt><dd>{formatDteBucket(selected.constraints.target_dte_bucket)}</dd></div><div><dt>Contract</dt><dd>{selected.constraints.selected_option_symbol || selected.constraints.selected_option_id || 'None selected'}</dd></div><div><dt>Source</dt><dd>{formatToken(selected.constraints.selected_option_source)}</dd></div><div><dt>Spread</dt><dd>{selected.constraints.selection_debug?.selected_option_snapshot?.spread_pct === undefined || selected.constraints.selection_debug?.selected_option_snapshot?.spread_pct === null ? 'N/A' : `${(selected.constraints.selection_debug.selected_option_snapshot.spread_pct * 100).toFixed(2)}%`}</dd></div><div><dt>Open interest</dt><dd>{selected.constraints.selection_debug?.selected_option_snapshot?.open_interest ?? 'N/A'}</dd></div><div><dt>Continuation</dt><dd>{selected.constraints.entry_gate?.allowed === true ? 'Allowed' : selected.constraints.entry_gate?.allowed === false ? 'Blocked' : 'Not recorded'}</dd></div><div><dt>Regime</dt><dd>{formatToken(selected.constraints.market_regime_gate?.regime_state || selected.constraints.market_regime?.state, { placeholder: 'Not recorded' })}</dd></div><div><dt>Regime gate</dt><dd>{selected.constraints.market_regime_gate?.allowed === true ? 'Allowed' : selected.constraints.market_regime_gate?.allowed === false ? 'Blocked' : 'Not recorded'}</dd></div></dl>{selected.constraints.market_regime_gate?.reason_codes?.length ? <p>Regime reasons: {formatTokenList(selected.constraints.market_regime_gate.reason_codes)}</p> : selected.constraints.entry_gate?.reason_codes?.length ? <p>Gate reasons: {formatTokenList(selected.constraints.entry_gate.reason_codes)}</p> : null}</article>
          </div>
          <section className="autopsy-evidence"><p className="autopsy-eyebrow">Grounded Evidence</p><h3>Inputs available at decision time</h3>{selected.evidence.length ? <ul>{selected.evidence.map((article) => <li key={article.article_id}><b>{article.title || `Article ${article.article_id}`}</b><span>{[article.source, article.article_scope, article.published_at ? formatDate(article.published_at) : ''].filter(Boolean).join(' · ')}</span></li>)}</ul> : <p>No compact article references were preserved for this decision.</p>}</section>
          <V2VerdictList available={payload.v2_verdicts_available === true} verdicts={selected.v2_verdicts} />
        </> : <p className="autopsy-empty">No decision records are available yet.</p>}
      </section>
      <aside className="autopsy-review">{selected ? <><div className="autopsy-review__safety">Read-only review. No order is executed here.</div><p className="autopsy-eyebrow">Outcome & Review</p><h2>{selected.review.headline}</h2><section><h3>What proved us wrong?</h3><p>{selected.review.what_proved_us_wrong}</p></section><section><h3>Recorded outcome</h3><dl><div><dt>Executed</dt><dd>{selected.outcome.trade_executed ? 'Yes' : 'No'}</dd></div><div><dt>Environment</dt><dd>{executionModeLabel(selected.execution?.mode ?? 'unlinked')}</dd></div><div><dt>P/L</dt><dd>{selected.outcome.pnl_pct === null ? 'Pending' : `${selected.outcome.pnl_pct.toFixed(2)}%`}</dd></div><div><dt>Outcome</dt><dd>{formatToken(selected.outcome.resolved_outcome_label, { placeholder: 'Pending' })}</dd></div><div><dt>Order</dt><dd>{selected.outcome.order_id || 'None'}</dd></div><div><dt>Status</dt><dd>{formatToken(selected.execution?.status, { placeholder: 'Unavailable' })}</dd></div><div><dt>Contract</dt><dd>{selected.execution?.option_symbol || selected.constraints.selected_option_symbol || 'Unavailable'}</dd></div><div><dt>Submitted</dt><dd>{selected.execution?.submitted_at ? formatDate(selected.execution.submitted_at) : 'Unavailable'}</dd></div></dl></section><section><h3>Pre-trade risks</h3>{selected.strategist.risks.length ? <ul>{selected.strategist.risks.map((risk) => <li key={risk}>{risk}</li>)}</ul> : <p>No risks were preserved.</p>}</section></> : null}</aside>
    </section>}
  </main>
}

export default DecisionAutopsyPage
