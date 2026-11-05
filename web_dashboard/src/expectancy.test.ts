import { describe, expect, it } from 'vitest'

import { pooledStats, sortCohorts } from './expectancy.ts'
import { HIGH_NO_SAMPLES, cohort, expectancyPayload } from './test/expectancyFixture.ts'

describe('pooledStats', () => {
  it('pools every cohort, weighting expectancy by resolved samples', () => {
    const payload = expectancyPayload({
      summary: { eligible_trade_count: 20, pnl_sample_count: 6, unresolved_count: 14 },
      cohorts: [
        cohort(),
        cohort({ key: 'low', label: 'Low', trade_count: 4, pnl_sample_count: 4, win_count: 3, loss_count: 1, expectancy_pct: 10, avg_win_pct: 20, avg_loss_pct: -20 }),
        HIGH_NO_SAMPLES,
      ],
    })

    const stats = pooledStats(payload)
    // (2 × −25.17 + 4 × 10) / 6
    expect(stats.expectancyPct).toBeCloseTo(-1.7233, 3)
    expect(stats.wins).toBe(3)
    expect(stats.samples).toBe(6)
    // wins 3 × 20 = 60; losses 2 × 25.17 + 1 × 20 = 70.34
    expect(stats.profitFactor).toBeCloseTo(60 / 70.34, 4)
  })

  it('has no expectancy or profit factor without resolved samples', () => {
    const stats = pooledStats(expectancyPayload({ summary: { eligible_trade_count: 14, pnl_sample_count: 0, unresolved_count: 14 }, cohorts: [HIGH_NO_SAMPLES] }))
    expect(stats.expectancyPct).toBeNull()
    expect(stats.profitFactor).toBeNull()
  })

  it('declines to pool when cohorts overlap, so the totals would not add up', () => {
    const payload = expectancyPayload({ summary: { eligible_trade_count: 2, pnl_sample_count: 2, unresolved_count: 0 }, cohorts: [cohort(), cohort({ key: 'tech' })] })
    expect(pooledStats(payload).expectancyPct).toBeNull()
  })
})

describe('sortCohorts', () => {
  const cohorts = [cohort(), HIGH_NO_SAMPLES, cohort({ key: 'low', label: 'Low', expectancy_pct: 4 })]

  it('puts cohorts without a value last whichever way numbers sort', () => {
    expect(sortCohorts(cohorts, 'expectancy_pct', false).map((row) => row.key)).toEqual(['low', 'medium', 'high'])
    expect(sortCohorts(cohorts, 'expectancy_pct', true).map((row) => row.key)).toEqual(['medium', 'low', 'high'])
  })

  it('sorts labels alphabetically', () => {
    expect(sortCohorts(cohorts, 'label', true).map((row) => row.label)).toEqual(['High', 'Low', 'Medium'])
  })
})
