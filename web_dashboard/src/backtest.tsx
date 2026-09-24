import { useEffect, useMemo, useState } from 'react'

import { formatDateTime, formatSignedPercent, hasNumericValue } from './format'
import './backtest.css'

/** Backtest tables print an em dash rather than "N/A" for metrics with no samples. */
const NO_METRIC_PLACEHOLDER = '—'

type Metrics = { event_count?: number; filled_count?: number; no_fill_count?: number; no_exit_quote_count?: number; portfolio_rejected_count?: number; fill_rate_pct?: number | null; win_rate_pct?: number | null; expectancy_pct?: number | null; profit_factor_like?: number | null; total_pnl_dollars?: number; total_commission_dollars?: number; total_slippage_dollars?: number; max_drawdown_dollars?: number; decision_count?: number; snapshot_count?: number; available_decision_count?: number; omitted_older_decision_count?: number; equity_curve?: { at: string; pnl_dollars: number }[] }
type Run = { id: number; strategy_version: string; model: string; prompt_version: string; started_at: string; completed_at: string; status: string; metrics: Metrics; config: Record<string, unknown>; error_message?: string }
type Event = { id: number; as_of: string; symbol: string; decision: string; option_symbol: string; status: string; evidence_snapshot_id: number | null; entry_at: string; exit_at: string; entry_price: number | null; exit_price: number | null; quantity: number | null; realized_pnl_pct: number | null; realized_pnl_dollars: number | null; commission_dollars: number | null; quote_provider: string | null; pricing_source: string | null; warnings: string[] }
type Detail = { run: Run; events: Event[]; read_only: boolean }
type EodQuoteFetch = { fetch_run_id: number; provider: string; pricing_source: string; status: string; source_decision_count: number; eligible_decision_count?: number; remaining_decision_count?: number; latest_contract_decision_at?: string | null; selected_start_at?: string | null; selected_end_at?: string | null; returned_quote_start_at?: string | null; returned_quote_end_at?: string | null; fetched_count: number; already_cached_count: number; no_data_count: number; unavailable_count: number; error_count: number; inserted_quote_count: number; invalid_quote_count: number; coverage_gaps: { reason: string }[]; read_only: boolean; no_trade_executed_by_feature: boolean }

function formatMetricPercent(value: number | null | undefined): string {
  return formatSignedPercent(value, { placeholder: NO_METRIC_PLACEHOLDER })
}

function formatMetricDollars(value: number | null | undefined): string {
  return hasNumericValue(value) ? `${value.toFixed(2)}$` : NO_METRIC_PLACEHOLDER
}

function formatDate(value: string): string {
  return formatDateTime(value, { placeholder: 'Unknown' })
}

async function getRuns(): Promise<Run[]> { const response = await fetch('/api/backtests?limit=25'); if (!response.ok) throw new Error(`Failed to load backtests: ${response.status}`); return (await response.json() as { runs: Run[] }).runs }
async function getRun(runId: number): Promise<Detail> { const response = await fetch(`/api/backtests/${runId}`); if (!response.ok) throw new Error(`Failed to load backtest: ${response.status}`); return response.json() as Promise<Detail> }
async function createRun(config: Record<string, unknown>, limit: number | null): Promise<Detail> { const response = await fetch('/api/backtests', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config, limit }) }); const body = await response.json().catch(() => null) as Detail | { error?: string } | null; if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to run backtest: ${response.status}`); return body as Detail }
async function fetchEodQuotes(limit: number, maxHoldHours: number): Promise<EodQuoteFetch> { const response = await fetch('/api/backtests/marketdata-eod', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ limit, max_hold_hours: maxHoldHours }) }); const body = await response.json().catch(() => null) as EodQuoteFetch | { error?: string } | null; if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to fetch EOD quotes: ${response.status}`); return body as EodQuoteFetch }

function BacktestDataGaps({ events, hasExpectancy }: { events: Event[]; hasExpectancy: boolean }) {
  const missingContracts = events.filter((event) => event.status === 'no_fill' && !event.option_symbol).length
  const missingEntryQuotes = events.filter((event) => event.warnings?.includes('no_valid_historical_entry_quote')).length
  const delayedEntries = events.filter((event) => event.warnings?.includes('next_quote_exceeds_entry_delay')).length
  const missingExits = events.filter((event) => event.status === 'no_exit_quote').length
  const rejected = events.filter((event) => event.status === 'portfolio_rejected').length
  if (hasExpectancy && !missingContracts && !missingEntryQuotes && !delayedEntries && !missingExits && !rejected) return null

  return <section className="backtest-page__data-gaps" aria-label="Backtest data gaps">
    <strong>{hasExpectancy ? 'Some decisions have no simulated outcome' : 'Expectancy unavailable: no closed simulated trades'}</strong>
    <span>Expectancy averages returns from closed simulations. The decision count measures records examined, not usable trade outcomes.</span>
    {!events.length ? <span>No call/put decisions were included in this run.</span> : null}
    <ul>
      {missingContracts > 0 ? <li>{missingContracts} decisions have no recorded option contract. Fetching quotes cannot resolve these; replay requires the contract selected at the time.</li> : null}
      {missingEntryQuotes > 0 ? <li>{missingEntryQuotes} decisions have no usable historical entry quote. Fetch EOD quotes for their contracts or review stored quote quality, then create a new run.</li> : null}
      {delayedEntries > 0 ? <li>{delayedEntries} decisions have their first usable quote outside the allowed entry window. They need earlier historical quotes.</li> : null}
      {missingExits > 0 ? <li>{missingExits} simulations have an entry but no usable exit quote within the holding period. Their returns are unknown.</li> : null}
      {rejected > 0 ? <li>{rejected} simulated entries were rejected by portfolio limits.</li> : null}
    </ul>
    <span>Simulation and quote-fetch limits are separate. Increasing decisions to simulate does not fetch more quotes. Saved runs do not update when quotes arrive.</span>
  </section>
}

function BacktestPage() {
  const [runs, setRuns] = useState<Run[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [fetchingQuotes, setFetchingQuotes] = useState(false)
  const [quoteFetch, setQuoteFetch] = useState<EodQuoteFetch | null>(null)
  const [allDecisions, setAllDecisions] = useState(true)
  const [decisionLimit, setDecisionLimit] = useState(250)
  const [fetchLimit, setFetchLimit] = useState(10)
  const [entrySlippage, setEntrySlippage] = useState(25)
  const [exitSlippage, setExitSlippage] = useState(25)
  const [commission, setCommission] = useState(0.65)
  const [takeProfit, setTakeProfit] = useState(50)
  const [stopLoss, setStopLoss] = useState(-50)
  const [maxHoldHours, setMaxHoldHours] = useState(168)
  const [maxConcurrentPositions, setMaxConcurrentPositions] = useState(5)
  const [maxTotalNotional, setMaxTotalNotional] = useState(50000)
  const [maxSectorNotionalPct, setMaxSectorNotionalPct] = useState(50)
  const [dailyLossLimit, setDailyLossLimit] = useState(2000)

  const loadRuns = () => getRuns().then((nextRuns) => { setRuns(nextRuns); return nextRuns })
  useEffect(() => { let active = true; loadRuns().then((nextRuns) => { if (active && nextRuns[0]) return getRun(nextRuns[0].id).then((next) => { if (active) setDetail(next) }) }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Failed to load backtests.') }).finally(() => { if (active) setLoading(false) }); return () => { active = false } }, [])
  const selectRun = (runId: number) => { setLoading(true); getRun(runId).then((next) => { setDetail(next); setError(null) }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Failed to load backtest.')).finally(() => setLoading(false)) }
  const startRun = () => { setRunning(true); createRun({ entry_slippage_bps: entrySlippage, exit_slippage_bps: exitSlippage, commission_per_contract: commission, take_profit_pct: takeProfit, stop_loss_pct: stopLoss, max_hold_hours: maxHoldHours, max_concurrent_positions: maxConcurrentPositions, max_total_entry_notional: maxTotalNotional, max_sector_notional_pct: maxSectorNotionalPct, daily_loss_limit_dollars: dailyLossLimit }, allDecisions ? null : decisionLimit).then((next) => { setDetail(next); return loadRuns() }).then(() => setError(null)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Failed to run backtest.')).finally(() => setRunning(false)) }
  const startEodFetch = () => { setFetchingQuotes(true); fetchEodQuotes(fetchLimit, maxHoldHours).then((result) => { setQuoteFetch(result); setError(null) }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Failed to fetch EOD quotes.')).finally(() => setFetchingQuotes(false)) }
  const equityMax = useMemo(() => Math.max(...(detail?.run.metrics.equity_curve?.map((point) => Math.abs(point.pnl_dollars)) ?? [1]), 1), [detail])

  return <main className="backtest-page">
    <section className="backtest-page__hero"><div><p>Evidence Time Machine</p><h1>Historical Decision Backtest</h1><span>Uses stored decision evidence and historical option quotes only. No live data, orders, or account state is used.</span></div><div className="backtest-page__hero-actions"><button className="backtest-page__fetch-button" type="button" onClick={startEodFetch} disabled={fetchingQuotes}>{fetchingQuotes ? 'Fetching EOD quotes…' : 'Fetch next EOD batch'}</button><button type="button" onClick={startRun} disabled={running}>{running ? 'Running…' : 'Run backtest'}</button></div></section>
    <section className="backtest-page__controls"><label>Decision window<select value={allDecisions ? 'all' : 'latest'} onChange={(event) => setAllDecisions(event.target.value === 'all')}><option value="all">All history</option><option value="latest">Latest decisions</option></select></label>{!allDecisions ? <label>Latest decisions to simulate<input type="number" min="1" max="1000" value={decisionLimit} onChange={(event) => setDecisionLimit(Number(event.target.value))} /></label> : null}<label>EOD batch size<input type="number" min="1" max="100" value={fetchLimit} onChange={(event) => setFetchLimit(Number(event.target.value))} /></label><label>Entry slippage (bps)<input type="number" min="0" value={entrySlippage} onChange={(event) => setEntrySlippage(Number(event.target.value))} /></label><label>Exit slippage (bps)<input type="number" min="0" value={exitSlippage} onChange={(event) => setExitSlippage(Number(event.target.value))} /></label><label>Commission / contract<input type="number" min="0" step="0.05" value={commission} onChange={(event) => setCommission(Number(event.target.value))} /></label><label>Take profit (%)<input type="number" min="0" step="1" value={takeProfit} onChange={(event) => setTakeProfit(Number(event.target.value))} /></label><label>Stop loss (%)<input type="number" max="0" step="1" value={stopLoss} onChange={(event) => setStopLoss(Number(event.target.value))} /></label><label>Max hold (hours)<input type="number" min="1" step="1" value={maxHoldHours} onChange={(event) => setMaxHoldHours(Number(event.target.value))} /></label><label>Max positions<input type="number" min="1" step="1" value={maxConcurrentPositions} onChange={(event) => setMaxConcurrentPositions(Number(event.target.value))} /></label><label>Portfolio notional ($)<input type="number" min="1" step="1000" value={maxTotalNotional} onChange={(event) => setMaxTotalNotional(Number(event.target.value))} /></label><label>Sector cap (%)<input type="number" min="1" max="100" step="1" value={maxSectorNotionalPct} onChange={(event) => setMaxSectorNotionalPct(Number(event.target.value))} /></label><label>Daily loss limit ($)<input type="number" min="1" step="100" value={dailyLossLimit} onChange={(event) => setDailyLossLimit(Number(event.target.value))} /></label><span>All history includes every stored call/put decision, simulated oldest to newest. Latest decisions limits the run to a recent window. Each EOD fetch advances through uncached contract windows across history; completed cached windows do not consume the batch limit. Portfolio limits reject new simulated entries once capacity, entry-notional, sector, or realized daily-loss limits are reached. A sector cap is measured against the portfolio-notional cap. MarketData.app EOD quotes are fetched server-side; the token never reaches your browser.</span></section>
    {quoteFetch ? <section className="backtest-page__quote-fetch"><div><p>EOD quote fetch #{quoteFetch.fetch_run_id}</p><strong>{quoteFetch.provider.replaceAll('_', ' ')} · {quoteFetch.status.replaceAll('_', ' ')}</strong><span>{quoteFetch.pricing_source.replaceAll('_', ' ')} · no trades are executed</span><span>Latest decision with a recorded contract: {quoteFetch.latest_contract_decision_at ? formatDate(quoteFetch.latest_contract_decision_at) : 'None'}.</span><span>Selected decision dates: {quoteFetch.selected_start_at && quoteFetch.selected_end_at ? `${formatDate(quoteFetch.selected_start_at)} → ${formatDate(quoteFetch.selected_end_at)}` : 'No windows selected'}.</span><span>Quotes returned in this batch: {quoteFetch.returned_quote_start_at && quoteFetch.returned_quote_end_at ? `${formatDate(quoteFetch.returned_quote_start_at)} → ${formatDate(quoteFetch.returned_quote_end_at)}` : 'None'}.</span><span>{quoteFetch.remaining_decision_count ?? 0} other windows pending. Fetch the next batch to continue, then create a new backtest.</span></div><dl><div><dt>Imported</dt><dd>{quoteFetch.inserted_quote_count} quotes</dd></div><div><dt>Cached</dt><dd>{quoteFetch.already_cached_count} decisions</dd></div><div><dt>Gaps</dt><dd>{quoteFetch.no_data_count + quoteFetch.unavailable_count + quoteFetch.error_count}</dd></div></dl></section> : null}
    {error ? <section className="backtest-page__empty">{error}</section> : null}
    {loading && !detail ? <section className="backtest-page__empty">Loading backtest ledger…</section> : null}
    <section className="backtest-page__layout">
      <aside className="backtest-page__runs"><p>Run history</p><h2>Immutable simulations</h2>{runs.length ? runs.map((run) => <button key={run.id} type="button" onClick={() => selectRun(run.id)} className={detail?.run.id === run.id ? 'backtest-page__run backtest-page__run--active' : 'backtest-page__run'}><strong>{run.strategy_version}</strong><span>{run.status.replaceAll('_', ' ')}</span><small>{formatDate(run.started_at)}</small><b>{formatMetricPercent(run.metrics.expectancy_pct ?? null)}</b></button>) : <span>No backtests have been run yet.</span>}</aside>
      <section className="backtest-page__detail">{detail ? <><header><div><p>Run #{detail.run.id}</p><h2>{detail.run.strategy_version}</h2><span>{detail.run.model || 'Stored decision model'} · {detail.run.prompt_version || 'Frozen input'}</span></div><span className="backtest-page__status">{detail.run.status}</span></header><div className="backtest-page__metrics"><article><span>Filled</span><b>{detail.run.metrics.filled_count ?? 0}</b><small>{formatMetricPercent(detail.run.metrics.fill_rate_pct ?? null)} fill rate</small></article><article><span>Expectancy</span><b>{formatMetricPercent(detail.run.metrics.expectancy_pct ?? null)}</b><small>{formatMetricPercent(detail.run.metrics.win_rate_pct ?? null)} win rate</small></article><article><span>Total P/L</span><b>{formatMetricDollars(detail.run.metrics.total_pnl_dollars ?? null)}</b><small>{formatMetricDollars(detail.run.metrics.max_drawdown_dollars ?? null)} max drawdown</small></article><article><span>Decisions examined</span><b>{detail.run.metrics.snapshot_count ?? 0}</b><small>{detail.run.metrics.no_fill_count ?? 0} no fills · {detail.run.metrics.portfolio_rejected_count ?? 0} guardrail rejects</small></article></div><p className="backtest-page__coverage">Decision dates: {detail.events.length ? `${formatDate(detail.events[0].as_of)} → ${formatDate(detail.events[detail.events.length - 1].as_of)}` : 'No matching decisions'}.{detail.run.metrics.available_decision_count !== undefined ? ` Included ${detail.run.metrics.decision_count ?? detail.events.length} of ${detail.run.metrics.available_decision_count} matching decisions.` : ''}{(detail.run.metrics.omitted_older_decision_count ?? 0) > 0 ? ` ${detail.run.metrics.omitted_older_decision_count} older decisions excluded by the limit.` : ''}</p><BacktestDataGaps events={detail.events} hasExpectancy={hasNumericValue(detail.run.metrics.expectancy_pct)} /><section className="backtest-page__equity"><div><p>Equity curve</p><h3>Simulated cumulative P/L</h3></div>{detail.run.metrics.equity_curve?.length ? <div className="backtest-page__bars">{detail.run.metrics.equity_curve.map((point) => <i key={`${point.at}-${point.pnl_dollars}`} className={point.pnl_dollars < 0 ? 'backtest-page__bar backtest-page__bar--loss' : 'backtest-page__bar'} style={{ height: `${Math.max(6, Math.abs(point.pnl_dollars) / equityMax * 100)}%` }} title={`${formatDate(point.at)}: ${formatMetricDollars(point.pnl_dollars)}`} />)}</div> : <span>No closed simulated fills yet.</span>}</section><section className="backtest-page__events"><header><div><p>Event ledger</p><h3>Evidence, contract, fill, and outcome audit</h3></div><span>Commissions {formatMetricDollars(detail.run.metrics.total_commission_dollars ?? null)} · Slippage {formatMetricDollars(detail.run.metrics.total_slippage_dollars ?? null)}</span></header><div><table><thead><tr><th>As of</th><th>Decision</th><th>Contract</th><th>Status</th><th>Source</th><th>Evidence</th><th>Entry → exit</th><th>P/L</th><th>Warnings</th></tr></thead><tbody>{detail.events.map((event) => <tr key={event.id}><td>{formatDate(event.as_of)}<small>{event.symbol}</small></td><td>{event.decision}</td><td>{event.option_symbol || '—'}<small>{event.quantity ?? 0} contracts</small></td><td>{event.status.replaceAll('_', ' ')}</td><td>{event.pricing_source || '—'}<small>{event.quote_provider || ''}</small></td><td>Snapshot #{event.evidence_snapshot_id ?? '—'}</td><td>{event.entry_price === null ? '—' : `${event.entry_price.toFixed(2)} → ${event.exit_price?.toFixed(2) ?? '—'}`}</td><td className={event.realized_pnl_pct !== null && event.realized_pnl_pct < 0 ? 'backtest-page__loss' : 'backtest-page__gain'}>{formatMetricPercent(event.realized_pnl_pct)}</td><td>{event.warnings?.join(', ') || '—'}</td></tr>)}</tbody></table></div>{!detail.events.length ? <p className="backtest-page__empty">No manager call/put decisions matched this run.</p> : null}</section></> : <section className="backtest-page__empty">Select a run to inspect its audit trail.</section>}</section>
    </section>
  </main>
}

export default BacktestPage
