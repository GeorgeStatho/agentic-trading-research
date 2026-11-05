/** `/api/backtests`: replays of stored decisions against historical option quotes. */

export type Metrics = { event_count?: number; filled_count?: number; no_fill_count?: number; no_exit_quote_count?: number; portfolio_rejected_count?: number; fill_rate_pct?: number | null; win_rate_pct?: number | null; expectancy_pct?: number | null; profit_factor_like?: number | null; total_pnl_dollars?: number; total_commission_dollars?: number; total_slippage_dollars?: number; max_drawdown_dollars?: number; decision_count?: number; snapshot_count?: number; available_decision_count?: number; omitted_older_decision_count?: number; equity_curve?: { at: string; pnl_dollars: number }[] }
export type Run = { id: number; strategy_version: string; model: string; prompt_version: string; started_at: string; completed_at: string; status: string; metrics: Metrics; config: Record<string, unknown>; error_message?: string }
export type BacktestEvent = { id: number; as_of: string; symbol: string; decision: string; option_symbol: string; status: string; evidence_snapshot_id: number | null; entry_at: string; exit_at: string; entry_price: number | null; exit_price: number | null; quantity: number | null; realized_pnl_pct: number | null; realized_pnl_dollars: number | null; commission_dollars: number | null; quote_provider: string | null; pricing_source: string | null; warnings: string[] }
export type Detail = { run: Run; events: BacktestEvent[]; read_only: boolean }
export type EodQuoteFetch = { fetch_run_id: number; provider: string; pricing_source: string; status: string; stop_reason?: string | null; resume_at?: string | null; budget?: { daily_limit: number; used: number; remaining: number; resets_at: string }; source_decision_count: number; eligible_decision_count?: number; remaining_decision_count?: number; latest_contract_decision_at?: string | null; selected_start_at?: string | null; selected_end_at?: string | null; returned_quote_start_at?: string | null; returned_quote_end_at?: string | null; fetched_count: number; already_cached_count: number; no_data_count: number; unavailable_count: number; error_count: number; inserted_quote_count: number; invalid_quote_count: number; coverage_gaps: { reason: string }[]; read_only: boolean; no_trade_executed_by_feature: boolean }

async function errorFrom(response: Response, fallback: string): Promise<Error> {
  const body = await response.json().catch(() => null) as { error?: string } | null
  return new Error(body?.error ?? `${fallback}: ${response.status}`)
}

export async function getRuns(): Promise<Run[]> {
  const response = await fetch('/api/backtests?limit=25')
  if (!response.ok) throw new Error(`Failed to load backtests: ${response.status}`)
  return (await response.json() as { runs: Run[] }).runs
}

export async function getRun(runId: number): Promise<Detail> {
  const response = await fetch(`/api/backtests/${runId}`)
  if (!response.ok) throw new Error(`Failed to load backtest: ${response.status}`)
  return response.json() as Promise<Detail>
}

export async function createRun(config: Record<string, unknown>, limit: number | null): Promise<Detail> {
  const response = await fetch('/api/backtests', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config, limit }) })
  if (!response.ok) throw await errorFrom(response, 'Failed to run backtest')
  return response.json() as Promise<Detail>
}

/** Fetches quotes from MarketData.app server-side, which spends the daily allowance. */
export async function fetchEodQuotes(limit: number, maxHoldHours: number): Promise<EodQuoteFetch> {
  const response = await fetch('/api/backtests/marketdata-eod', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ limit, max_hold_hours: maxHoldHours }) })
  if (!response.ok) throw await errorFrom(response, 'Failed to fetch EOD quotes')
  return response.json() as Promise<EodQuoteFetch>
}

const OUTCOME_LABELS: Record<string, string> = {
  simulated_closed: 'filled',
  simulated_open: 'filled, still open',
  portfolio_rejected: 'guardrail rejects',
  no_exit_quote: 'no exit quote',
  no_fill: 'no fill',
}

export type Outcome = { status: string; label: string; count: number }

/**
 * How many of the run's decisions ended in each state, fills first.
 *
 * Counted from events rather than metrics: `no_fill_count` already includes
 * the guardrail rejects, so the metrics would count those twice.
 */
export function decisionOutcomes(events: readonly BacktestEvent[]): Outcome[] {
  const counts = new Map<string, number>()
  for (const event of events) counts.set(event.status, (counts.get(event.status) ?? 0) + 1)
  const order = Object.keys(OUTCOME_LABELS)
  const rank = (status: string) => (order.includes(status) ? order.indexOf(status) : order.length)
  return Array.from(counts, ([status, count]) => ({ status, label: OUTCOME_LABELS[status] ?? status.replaceAll('_', ' '), count }))
    .sort((left, right) => rank(left.status) - rank(right.status))
}

/** What the run's data could not simulate, one reason per entry; empty when nothing is missing. */
export function dataGaps(events: readonly BacktestEvent[], hasExpectancy: boolean): string[] {
  const count = (test: (event: BacktestEvent) => boolean) => events.filter(test).length
  const reasons = [
    [count((event) => event.status === 'no_fill' && !event.option_symbol), 'decisions have no recorded option contract (quotes cannot fix these)'],
    [count((event) => event.warnings?.includes('no_valid_historical_entry_quote')), 'have no usable historical entry quote (fetch EOD quotes, then run again)'],
    [count((event) => event.warnings?.includes('next_quote_exceeds_entry_delay')), 'have their first usable quote outside the entry window'],
    [count((event) => event.status === 'no_exit_quote'), 'entries have no exit quote within the holding period'],
    [count((event) => event.status === 'portfolio_rejected'), 'entries were rejected by portfolio limits'],
  ] as const
  const lines = reasons.filter(([n]) => n > 0).map(([n, text]) => `${n} ${text}`)
  if (!events.length) lines.push('no call/put decisions were included in this run')
  if (hasExpectancy && !lines.length) return []
  return [hasExpectancy ? 'Some decisions have no simulated outcome' : 'Expectancy unavailable: no closed simulated trades', ...lines]
}
