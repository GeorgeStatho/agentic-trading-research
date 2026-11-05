import { describe, expect, it } from 'vitest'

import { drawdownPct, equityPoints, pointsInRange, tradeMarkers } from './equitySeries.ts'
import type { MarkerTrade } from './equitySeries.ts'

// Session timestamps are ET midnight in epoch seconds, as /api/portfolio-history returns them.
const OCT_22 = 1792641600 // 2026-10-22 00:00 ET
const DAY = 86_400

const HISTORY = {
  timestamp: [OCT_22, OCT_22 + DAY, OCT_22 + 2 * DAY, OCT_22 + 8 * DAY, OCT_22 + 9 * DAY],
  equity: [0, 3000, 3000, 2877.94, 2404.85],
  profit_loss: [0, 0, 0, -122.06, -473.09],
}

describe('equityPoints', () => {
  it('starts at the first funded session, not at the empty account', () => {
    const points = equityPoints(HISTORY)
    expect(points.map((point) => point.equity)).toEqual([3000, 3000, 2877.94, 2404.85])
    expect(points[0].time).toBe((OCT_22 + DAY) * 1000)
  })

  it('carries the daily P/L the API reports, which excludes deposits', () => {
    expect(equityPoints(HISTORY).map((point) => point.pnl)).toEqual([0, 0, -122.06, -473.09])
  })

  it('is empty when the account was never funded', () => {
    expect(equityPoints({ timestamp: [OCT_22], equity: [0], profit_loss: [0] })).toEqual([])
  })
})

describe('pointsInRange', () => {
  const points = equityPoints(HISTORY)

  it('keeps everything for All', () => {
    expect(pointsInRange(points, 'All')).toHaveLength(4)
  })

  it('keeps sessions within 7 calendar days of the latest one for 1W', () => {
    expect(pointsInRange(points, '1W').map((point) => point.equity)).toEqual([3000, 2877.94, 2404.85])
  })
})

describe('drawdownPct', () => {
  it('measures each point against the running peak', () => {
    const points = [100, 120, 90, 130, 117].map((equity, index) => ({ time: index, equity, pnl: 0 }))
    expect(drawdownPct(points)).toEqual([0, 0, -25, 0, -10])
  })
})

describe('tradeMarkers', () => {
  const points = equityPoints(HISTORY)
  const trade = (overrides: Partial<MarkerTrade>): MarkerTrade => ({
    underlying_symbol: 'TSLA',
    decision: 'call',
    order_side: 'buy',
    order_status: 'filled',
    submitted_at: '2026-10-31T15:21:00+00:00',
    ...overrides,
  })

  it('puts an entry marker on the session the order was placed, labelled ticker and side', () => {
    expect(tradeMarkers(points, [trade({})])).toEqual([
      { time: (OCT_22 + 9 * DAY) * 1000, equity: 2404.85, kind: 'entry', label: 'TSLA C' },
    ])
  })

  it('marks sells as exits, labelled by the exit rule', () => {
    const [marker] = tradeMarkers(points, [trade({ order_side: 'sell', decision: 'stop_loss', submitted_at: '2026-10-30T13:32:00+00:00' })])
    expect(marker).toMatchObject({ kind: 'exit', label: 'TSLA stop loss', equity: 2877.94 })
  })

  it('skips canceled orders and orders outside the plotted sessions', () => {
    expect(tradeMarkers(points, [
      trade({ order_status: 'canceled' }),
      trade({ submitted_at: '2026-09-01T15:00:00+00:00' }),
    ])).toEqual([])
  })
})
