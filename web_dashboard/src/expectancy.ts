/** `/api/decision-expectancy`: P/L of executed decisions, grouped into cohorts. */

export type Basis = 'marked' | 'closed'
export type GroupBy = 'confidence' | 'dte_bucket' | 'selector_mode' | 'sector' | 'time_of_day' | 'confidence_x_dte_bucket' | 'confidence_x_selector_mode' | 'entry_gate' | 'market_regime' | 'evidence_count_bucket' | 'trend_state' | 'relative_strength_state' | 'volume_state' | 'ticker' | 'industry' | 'exit_reason' | 'catalyst_family' | 'holding_period'
export type SortKey = 'label' | 'trade_count' | 'pnl_sample_count' | 'win_rate_pct' | 'expectancy_pct'

export type Cohort = {
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

export type ExpectancyPayload = {
  as_of: string
  basis: Basis
  basis_label: string
  filters: { group_by: GroupBy; within_days: number; min_samples: number; decision: string; confidence: string; sector: string | null }
  summary: { eligible_trade_count: number; pnl_sample_count: number; unresolved_count: number }
  cohorts: Cohort[]
  read_only: boolean
}

export type ExpectancyFilters = { basis: Basis; groupBy: GroupBy; withinDays: number; minSamples: number; decision: string; confidence: string; sector: string }

export async function getExpectancy(filters: ExpectancyFilters): Promise<ExpectancyPayload> {
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
  const body = await response.json().catch(() => null) as ExpectancyPayload | { error?: string } | null
  if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to load expectancy: ${response.status}`)
  return body as ExpectancyPayload
}

export type PooledStats = {
  /** Sample-weighted mean of the cohorts' expectancy; null when nothing resolved. */
  expectancyPct: number | null
  wins: number
  samples: number
  profitFactor: number | null
}

/** All cohorts as one: the stat strip's headline numbers. */
export function pooledStats(payload: ExpectancyPayload): PooledStats {
  let samples = 0
  let wins = 0
  let weighted = 0
  let grossWin = 0
  let grossLoss = 0
  for (const cohort of payload.cohorts) {
    samples += cohort.pnl_sample_count
    wins += cohort.win_count
    weighted += (cohort.expectancy_pct ?? 0) * cohort.pnl_sample_count
    grossWin += (cohort.avg_win_pct ?? 0) * cohort.win_count
    grossLoss += Math.abs(cohort.avg_loss_pct ?? 0) * cohort.loss_count
  }
  // A trade counted in two cohorts would be double-weighted, so only pool when the cohorts add up to the summary.
  if (samples === 0 || samples !== payload.summary.pnl_sample_count) {
    return { expectancyPct: null, wins, samples, profitFactor: null }
  }
  return { expectancyPct: weighted / samples, wins, samples, profitFactor: grossLoss > 0 ? grossWin / grossLoss : null }
}

/** Sorted by one column; cohorts with no value for it go last either way. */
export function sortCohorts(cohorts: readonly Cohort[], key: SortKey, ascending: boolean): Cohort[] {
  return [...cohorts].sort((left, right) => {
    if (key === 'label') {
      const comparison = left.label.localeCompare(right.label)
      return ascending ? comparison : -comparison
    }
    const leftValue = left[key]
    const rightValue = right[key]
    if (leftValue === null || rightValue === null) {
      return Number(leftValue === null) - Number(rightValue === null)
    }
    return ascending ? leftValue - rightValue : rightValue - leftValue
  })
}
