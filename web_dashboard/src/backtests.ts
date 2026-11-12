/** `/api/backtests`: replays of stored decisions against historical option quotes. */

export type Metrics = { event_count?: number; filled_count?: number; no_fill_count?: number; no_exit_quote_count?: number; portfolio_rejected_count?: number; fill_rate_pct?: number | null; win_rate_pct?: number | null; expectancy_pct?: number | null; profit_factor_like?: number | null; total_pnl_dollars?: number; total_commission_dollars?: number; total_slippage_dollars?: number; max_drawdown_dollars?: number; median_pnl_pct?: number | null; pricing_source_counts?: Record<string, number>; before_portfolio_limits?: { priced_entry_count: number; symbol_count: number; expectancy_pct: number | null; median_pnl_pct: number | null; win_rate_pct: number | null }; decision_start_at?: string | null; decision_end_at?: string | null; decision_limit?: number | null; replay?: ReplaySummary; decision_count?: number; snapshot_count?: number; available_decision_count?: number; omitted_older_decision_count?: number; equity_curve?: { at: string; pnl_dollars: number }[] }
/** `metrics.replay` on LLM replay runs (llm_backtest.py); every field optional, older runs lack the newer ones. */
export type ReplaySummary = { source_decision_count?: number; missing_frozen_input_count?: number; excluded_pre_knowledge_cutoff?: number; contamination?: Record<string, unknown> | null; min_hours_between_same_symbol?: number; spaced_out_decision_count?: number; outcomes?: Record<string, number>; changed_direction_count?: number; cached_answer_count?: number; provider?: string; sample?: number; selector_version?: string; strategist_replay?: { model: string; prompt_version: string; changed_decision_count: number; cached_answer_count: number; reuse_max_age_minutes: number; reused_answer_count: number } | null; thesis_recheck?: { rechecked: number; changed: number } | null; production_settings?: { recorded_decision_count?: number; local_fallback_decision_count?: number; execute_medium_confidence_trades?: boolean; overrides?: string[]; selector_settings_differing_from_local?: string[] }; daily_bars?: { still_missing?: string[] } }
export type Run = { id: number; code_revision?: string; strategy_version: string; model: string; prompt_version: string; started_at: string; completed_at: string; status: string; metrics: Metrics; config: Record<string, unknown>; error_message?: string }
export type BacktestEvent = { id: number; as_of: string; symbol: string; decision: string; option_symbol: string; status: string; evidence_snapshot_id: number | null; entry_at: string; exit_at: string; entry_price: number | null; exit_price: number | null; quantity: number | null; realized_pnl_pct: number | null; realized_pnl_dollars: number | null; commission_dollars: number | null; quote_provider: string | null; pricing_source: string | null; warnings: string[] }
export type Detail = { run: Run; events: BacktestEvent[]; read_only: boolean }
export type EodQuoteFetch = { fetch_run_id: number; provider: string; pricing_source: string; status: string; stop_reason?: string | null; resume_at?: string | null; budget?: { daily_limit: number; used: number; remaining: number; resets_at: string }; source_decision_count: number; eligible_decision_count?: number; remaining_decision_count?: number; latest_contract_decision_at?: string | null; selected_start_at?: string | null; selected_end_at?: string | null; returned_quote_start_at?: string | null; returned_quote_end_at?: string | null; fetched_count: number; already_cached_count: number; no_data_count: number; unavailable_count: number; error_count: number; inserted_quote_count: number; invalid_quote_count: number; coverage_gaps: { reason: string }[]; read_only: boolean; no_trade_executed_by_feature: boolean }

async function errorFrom(response: Response, fallback: string): Promise<Error> {
  const body = await response.json().catch(() => null) as { error?: string } | null
  return new Error(body?.error ?? `${fallback}: ${response.status}`)
}

export async function getRuns(): Promise<Run[]> {
  const response = await fetch('/api/backtests?limit=100')
  if (!response.ok) throw new Error(`Failed to load backtests: ${response.status}`)
  return (await response.json() as { runs: Run[] }).runs
}

export async function getRun(runId: number): Promise<Detail> {
  const response = await fetch(`/api/backtests/${runId}`)
  if (!response.ok) throw new Error(`Failed to load backtest: ${response.status}`)
  return response.json() as Promise<Detail>
}

/** A decision window: the latest N decisions, or a date range (null bounds are open). */
export type DecisionWindow = { limit: number | null; start_at?: string | null; end_at?: string | null }

export async function createRun(config: Record<string, unknown>, window: DecisionWindow): Promise<Detail> {
  const response = await fetch('/api/backtests', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config, ...window }) })
  if (!response.ok) throw await errorFrom(response, 'Failed to run backtest')
  return response.json() as Promise<Detail>
}

/** Fetches quotes from MarketData.app server-side, which spends the daily allowance. */
export async function fetchEodQuotes(limit: number, maxHoldHours: number, window: Omit<DecisionWindow, 'limit'> = {}): Promise<EodQuoteFetch> {
  const response = await fetch('/api/backtests/marketdata-eod', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ limit, max_hold_hours: maxHoldHours, ...window }) })
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

export type RunKind = 'stored' | 'llm'

export function runKind(run: Run): RunKind {
  return run.strategy_version?.startsWith('llm-replay') ? 'llm' : 'stored'
}

export type RunSortKey = 'id' | 'filled_count' | 'expectancy_pct' | 'total_pnl_dollars'

/** Runs ordered by `key`; runs without that metric (no fills) always go last. */
export function sortRuns(runs: readonly Run[], key: RunSortKey, ascending: boolean): Run[] {
  const value = (run: Run): number | null => (key === 'id' ? run.id : run.metrics[key] ?? null)
  return [...runs].sort((left, right) => {
    const leftValue = value(left)
    const rightValue = value(right)
    if (leftValue === null || rightValue === null) return Number(leftValue === null) - Number(rightValue === null)
    return ascending ? leftValue - rightValue : rightValue - leftValue
  })
}
