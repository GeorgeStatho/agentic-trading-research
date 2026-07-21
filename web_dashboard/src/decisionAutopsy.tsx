import { useEffect, useMemo, useState } from 'react'
import './decisionAutopsy.css'

type Article = { article_id: number; title: string; source: string; published_at: string; article_scope: string }
type Claim = { claim_type?: string; claim?: string; evidence_article_ids?: number[] }
type Record = {
  id: number
  symbol: string
  company_name: string
  decision_run_at: string
  strategist: { decision: string; confidence: string; model: string; summary: string; thesis: string[]; risks: string[]; evidence_claims: Claim[] }
  manager: { decision: string; confidence: string; reason: string }
  evidence: Article[]
  constraints: { target_dte_bucket: string; selected_option_id: string; selected_option_symbol: string; selected_expiration_date: string; selected_strike_price: number | null; selected_option_source: string }
  outcome: { trade_executed: boolean; order_id: string; pnl_pct: number | null; pnl_updated_at: string; resolved_outcome_label: string }
  review: { status: string; headline: string; what_proved_us_wrong: string }
}

type Payload = { as_of: string; count: number; records: Record[]; read_only: boolean; no_trade_executed_by_feature: boolean }

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Unknown time' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function tone(value: string): string {
  const normalized = value.toLowerCase()
  if (normalized.includes('adverse') || normalized === 'put') return 'negative'
  if (normalized.includes('favorable') || normalized === 'call' || normalized.includes('candidate')) return 'positive'
  return 'neutral'
}

async function getAutopsies(): Promise<Payload> {
  const response = await fetch('/api/decision-autopsies?limit=100')
  if (!response.ok) throw new Error(`Failed to load ledger: ${response.status}`)
  return response.json() as Promise<Payload>
}

function DecisionAutopsyPage() {
  const [payload, setPayload] = useState<Payload | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    getAutopsies().then((next) => {
      if (!active) return
      setPayload(next)
      setSelectedId(next.records[0]?.id ?? null)
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Failed to load ledger.')
    })
    return () => { active = false }
  }, [])

  const selected = useMemo(() => payload?.records.find((record) => record.id === selectedId) ?? payload?.records[0] ?? null, [payload, selectedId])
  const counts = useMemo(() => ({
    proposed: payload?.records.length ?? 0,
    executed: payload?.records.filter((record) => record.outcome.trade_executed).length ?? 0,
    adverse: payload?.records.filter((record) => record.review.status === 'adverse_outcome').length ?? 0,
  }), [payload])

  return <main className="autopsy-page">
    <section className="autopsy-hero">
      <div><p>Decision Autopsy</p><h1>AI Accountability Ledger</h1><span>Immutable decision-time evidence snapshots. Read only—this feature never submits an order.</span></div>
      <div className="autopsy-hero__metrics"><article><span>Proposals</span><b>{counts.proposed}</b></article><article><span>Executed</span><b>{counts.executed}</b></article><article><span>Adverse outcomes</span><b>{counts.adverse}</b></article></div>
    </section>
    {error ? <section className="autopsy-empty">{error}</section> : null}
    {!error && !payload ? <section className="autopsy-empty">Loading immutable decision records…</section> : null}
    {payload && <section className="autopsy-layout">
      <aside className="autopsy-queue"><p className="autopsy-eyebrow">Decision Queue</p><h2>Proposals</h2>{payload.records.map((record) => <button key={record.id} type="button" onClick={() => setSelectedId(record.id)} className={record.id === selected?.id ? 'autopsy-queue__item autopsy-queue__item--active' : 'autopsy-queue__item'}><span>{record.symbol} · {record.manager.decision || 'No trade'}</span><strong>{record.company_name || 'Stored proposal'}</strong><small>{formatDate(record.decision_run_at)}</small><i className={`autopsy-status autopsy-status--${tone(record.review.status)}`}>{record.review.status.replaceAll('_', ' ')}</i></button>)}</aside>
      <section className="autopsy-detail">
        {selected ? <>
          <header className="autopsy-detail__header"><div><p className="autopsy-eyebrow">Immutable Snapshot #{selected.id}</p><h2>{selected.symbol} · {selected.manager.decision || 'No trade'}</h2><span>{selected.company_name} · {formatDate(selected.decision_run_at)}</span></div><div className={`autopsy-decision autopsy-decision--${tone(selected.manager.decision)}`}>{selected.manager.confidence || 'Unrated'} confidence</div></header>
          <div className="autopsy-detail__grid">
            <article><p className="autopsy-eyebrow">GPT Thesis</p><h3>{selected.strategist.decision || 'No strategist decision'}</h3><p>{selected.strategist.summary || 'No strategist summary was preserved.'}</p>{selected.strategist.thesis.length ? <ul>{selected.strategist.thesis.map((item) => <li key={item}>{item}</li>)}</ul> : null}<small>{selected.strategist.model ? `Model ${selected.strategist.model}` : 'Stored strategist output'}</small></article>
            <article><p className="autopsy-eyebrow">Manager Permission</p><h3>{selected.manager.decision || 'No trade'}</h3><p>{selected.manager.reason || 'No manager reason was preserved.'}</p><dl><div><dt>Confidence</dt><dd>{selected.manager.confidence || 'N/A'}</dd></div><div><dt>Target DTE</dt><dd>{selected.constraints.target_dte_bucket || 'N/A'}</dd></div><div><dt>Contract</dt><dd>{selected.constraints.selected_option_symbol || selected.constraints.selected_option_id || 'None selected'}</dd></div><div><dt>Source</dt><dd>{selected.constraints.selected_option_source || 'N/A'}</dd></div></dl></article>
          </div>
          <section className="autopsy-evidence"><p className="autopsy-eyebrow">Grounded Evidence</p><h3>Inputs available at decision time</h3>{selected.evidence.length ? <ul>{selected.evidence.map((article) => <li key={article.article_id}><b>{article.title || `Article ${article.article_id}`}</b><span>{[article.source, article.article_scope, article.published_at ? formatDate(article.published_at) : ''].filter(Boolean).join(' · ')}</span></li>)}</ul> : <p>No compact article references were preserved for this decision.</p>}</section>
        </> : <p className="autopsy-empty">No decision records are available yet.</p>}
      </section>
      <aside className="autopsy-review">{selected ? <><div className="autopsy-review__safety">Read-only review. No order is executed here.</div><p className="autopsy-eyebrow">Outcome & Review</p><h2>{selected.review.headline}</h2><section><h3>What proved us wrong?</h3><p>{selected.review.what_proved_us_wrong}</p></section><section><h3>Recorded outcome</h3><dl><div><dt>Executed</dt><dd>{selected.outcome.trade_executed ? 'Yes' : 'No'}</dd></div><div><dt>P/L</dt><dd>{selected.outcome.pnl_pct === null ? 'Pending' : `${selected.outcome.pnl_pct.toFixed(2)}%`}</dd></div><div><dt>Outcome</dt><dd>{selected.outcome.resolved_outcome_label || 'Pending'}</dd></div><div><dt>Order</dt><dd>{selected.outcome.order_id || 'None'}</dd></div></dl></section><section><h3>Pre-trade risks</h3>{selected.strategist.risks.length ? <ul>{selected.strategist.risks.map((risk) => <li key={risk}>{risk}</li>)}</ul> : <p>No risks were preserved.</p>}</section></> : null}</aside>
    </section>}
  </main>
}

export default DecisionAutopsyPage
