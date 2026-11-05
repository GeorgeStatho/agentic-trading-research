import type { Cohort, ExpectancyPayload } from '../expectancy.ts'

/** Shaped like `/api/decision-expectancy` on Nov 4, 2026: two cohorts, both under the guardrail. */
export function cohort(overrides: Partial<Cohort> = {}): Cohort {
  return {
    key: 'medium',
    label: 'Medium',
    trade_count: 2,
    pnl_sample_count: 2,
    unresolved_count: 0,
    win_count: 0,
    loss_count: 2,
    flat_count: 0,
    win_rate_pct: 0,
    avg_pnl_pct: -25.17,
    median_pnl_pct: -25.17,
    avg_win_pct: null,
    avg_loss_pct: -25.17,
    expectancy_pct: -25.17,
    profit_factor_like: 0,
    insufficient_sample: true,
    latest_decision_at: '2026-11-02T17:30:03.523762+00:00',
    ...overrides,
  }
}

export const HIGH_NO_SAMPLES = cohort({
  key: 'high', label: 'High', trade_count: 14, pnl_sample_count: 0, unresolved_count: 14, loss_count: 0,
  win_rate_pct: null, avg_pnl_pct: null, median_pnl_pct: null, avg_loss_pct: null, expectancy_pct: null,
  profit_factor_like: null, latest_decision_at: '2026-09-29T14:34:15.940720+00:00',
})

export function expectancyPayload(overrides: Partial<ExpectancyPayload> = {}): ExpectancyPayload {
  return {
    as_of: '2026-11-04T19:32:03.041881+00:00',
    basis: 'marked',
    basis_label: 'Marked P/L, not realized P/L',
    filters: { group_by: 'confidence', within_days: 90, min_samples: 5, decision: 'all', confidence: 'all', sector: null },
    summary: { eligible_trade_count: 16, pnl_sample_count: 2, unresolved_count: 14 },
    cohorts: [cohort(), HIGH_NO_SAMPLES],
    read_only: true,
    ...overrides,
  }
}
