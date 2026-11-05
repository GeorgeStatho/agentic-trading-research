import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DailyPnlChart } from './ui/charts/DailyPnlChart.tsx'
import { DrawdownChart } from './ui/charts/DrawdownChart.tsx'
import { EquityChart } from './ui/charts/EquityChart.tsx'

const DAY_MS = 86_400_000
const points = [3000, 3000, 2877.94, 2404.85, 2604.79].map((equity, index) => ({
  time: Date.UTC(2026, 9, 23) + index * DAY_MS,
  equity,
  pnl: [0, 0, -122.06, -473.09, 199.94][index],
}))

describe('charts state their takeaway for screen readers', () => {
  it('equity: where it stands and how far it has moved over the range', () => {
    render(<EquityChart points={points} markers={[]} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe('Equity $2,604.79, -13.17% over the range shown')
  })

  it('drawdown: the deepest drop over the range', () => {
    render(<DrawdownChart points={points} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe('Max drawdown 19.84% over the range shown')
  })

  it('daily P/L: how many sessions were up and down', () => {
    render(<DailyPnlChart points={points} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe('5 sessions: 1 up, 2 down')
  })
})
