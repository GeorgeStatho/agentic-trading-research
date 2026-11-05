import { describe, expect, it } from 'vitest'

import { buildLiveStats } from './liveStats.ts'
import { kpiPayload } from './test/kpiFixture.ts'

function stat(label: string, payload = kpiPayload()) {
  const found = buildLiveStats(payload).find((item) => item.label === label)
  if (!found) throw new Error(`no "${label}" stat`)
  return found
}

describe('buildLiveStats', () => {
  it('lists the runtime stats in reading order', () => {
    expect(buildLiveStats(kpiPayload()).map((item) => item.label)).toEqual([
      'Equity', 'Day P/L', 'Total return', 'Holdings', 'Buying power', 'Max drawdown', 'Win rate', 'Expectancy', 'Regime',
    ])
  })

  it('adds unrealized P/L after total return once the API returns it', () => {
    const labels = buildLiveStats(kpiPayload({ unrealized_pl: 41.5 })).map((item) => item.label)
    expect(labels.slice(2, 4)).toEqual(['Total return', 'Unrealized'])
    expect(stat('Unrealized', kpiPayload({ unrealized_pl: 41.5 }))).toMatchObject({ value: '+$41.50', tone: 'gain' })
  })

  it('colors day P/L and total return by sign, and leaves zero uncolored', () => {
    expect(stat('Day P/L')).toMatchObject({ value: '-$225.02', sub: '-8.64%', tone: 'loss' })
    expect(stat('Day P/L', kpiPayload({ day_pl: 12, day_pl_pct: 0.5 }))).toMatchObject({ value: '+$12.00', sub: '+0.50%', tone: 'gain' })
    expect(stat('Day P/L', kpiPayload({ day_pl: 0, day_pl_pct: 0 })).tone).toBeUndefined()
    expect(stat('Total return')).toMatchObject({ value: '-13.17%', tone: 'loss' })
  })

  it('shows options holdings as value and share of equity', () => {
    const payload = kpiPayload({ options_exposure: { market_value: 412, equity_pct: 17.3, position_count: 1 } })
    expect(stat('Holdings', payload)).toMatchObject({ value: '$412.00', sub: '17.30% of equity' })
  })

  it('reads drawdown as a loss whenever there is one', () => {
    expect(stat('Max drawdown')).toMatchObject({ value: '19.84%', tone: 'loss' })
    expect(stat('Max drawdown', kpiPayload({ max_drawdown_pct: 0 })).tone).toBeUndefined()
  })

  it('mutes win rate and expectancy with n= until there are 5 closed trades', () => {
    expect(stat('Win rate')).toMatchObject({ value: '0 / 2', sub: 'n=2', tone: 'muted' })
    expect(stat('Expectancy')).toMatchObject({ value: '-24.24%', sub: 'n=2', tone: 'muted' })
  })

  it('shows the rate once the sample is big enough', () => {
    const payload = kpiPayload({ win_rate: { wins: 3, closed_trades: 6, win_rate_pct: 50 } })
    expect(stat('Win rate', payload)).toMatchObject({ value: '3 / 6', sub: '50%' })
    expect(stat('Win rate', payload).tone).toBeUndefined()
    expect(stat('Expectancy', payload)).toMatchObject({ value: '-24.24%', sub: 'avg per trade', tone: 'loss' })
  })

  it('keeps the regime neutral: high volatility is not a loss', () => {
    expect(stat('Regime')).toMatchObject({ value: 'high volatility' })
    expect(stat('Regime').tone).toBeUndefined()
  })
})
