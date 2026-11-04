import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { LagScatter } from './ui/charts/LagScatter.tsx'
import { lagPoints } from './ui/charts/lagPoints.ts'

const trades = [
  { symbol: 'NVDA', lag_hours: 1.5, option_pnl_pct: 98 },
  { symbol: 'SKHY', lag_hours: 2.8, option_pnl_pct: -38 },
  { symbol: 'AMD', lag_hours: null, option_pnl_pct: -10 },
  { symbol: 'TSLA', lag_hours: 4, option_pnl_pct: null },
]

describe('lagPoints', () => {
  it('plots only entries with both a measured lag and a closed P/L', () => {
    expect(lagPoints(trades)).toEqual([
      { symbol: 'NVDA', lag: 1.5, pnl: 98 },
      { symbol: 'SKHY', lag: 2.8, pnl: -38 },
    ])
  })
})

describe('LagScatter', () => {
  it('states the median lag and how many entries were within the first hour', () => {
    render(<LagScatter trades={trades} medianLagHours={3.93} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe('Median news-to-order lag 3.9 h; 0 of 2 plotted entries within the first hour')
  })
})
