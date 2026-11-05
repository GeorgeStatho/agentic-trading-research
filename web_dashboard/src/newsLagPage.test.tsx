import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import NewsLagPage from './newsLag.tsx'

function trade(overrides: Record<string, unknown> = {}) {
  return {
    order_id: 'a', symbol: 'NVDA', direction: 'call', ordered_at: '2026-08-18T15:00:00+00:00',
    lag_hours: 1.5, lag_bucket: '1-6h', pre_entry_move_pct: null, post_entry_move_pct: -3.63, pre_entry_share: null,
    closed: true, option_pnl_pct: -30.33, favourable_but_lost: false, exit_reason: 'stop_loss',
    ...overrides,
  }
}

function stats(n = 0) {
  return { n, mean: null, median: null }
}

function bucket(trades = 0, closed = 0) {
  return {
    trades, closed, option_pnl_pct: { ...stats(closed), win_rate: null, total: null },
    pre_entry_move_pct: stats(), post_entry_move_pct: stats(), pre_entry_share: stats(), daily_sampled_publish_prices: 0,
  }
}

function payload() {
  return {
    as_of: '2026-11-04T19:00:00+00:00',
    headline: { entries: 48, dated_entries: 21, median_lag_hours: 3.93, within_1h: 0, median_pre_entry_share: 0.3315, favourable_closed: 21, favourable_but_lost: 8, measurable: 10 },
    buckets: { '<1h': bucket(), '1-6h': bucket(12, 9), overnight: bucket(3, 2), '>1d': bucket(6, 5), unknown: bucket(27, 15) },
    all: { ...bucket(48, 31), daily_sampled_publish_prices: 12 },
    coverage: { entries: 48, missing: { no_citations: 16, open_or_no_pnl: 17 }, future_dated_citations: 3 },
    trades: [
      trade({ order_id: 'b', ordered_at: '2026-09-01T15:00:00+00:00', lag_hours: 2.1, pre_entry_move_pct: -1.44, post_entry_move_pct: 7.86, option_pnl_pct: 112.59, exit_reason: 'trailing_profit_stop' }),
      trade(),
      trade({ order_id: 'c', symbol: 'SKHY', lag_hours: 2.7, post_entry_move_pct: 3.17, option_pnl_pct: -23.85, favourable_but_lost: true }),
      trade({ order_id: 'd', symbol: 'BAC', lag_hours: null, lag_bucket: 'unknown', closed: false, option_pnl_pct: null, post_entry_move_pct: null }),
    ],
  }
}

const requests: string[] = []

beforeEach(() => {
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(String(input))
    return Response.json(payload())
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function stat(label: string): string {
  return screen.getByText(label, { selector: 'div' }).parentElement!.textContent ?? ''
}

async function loaded() {
  return screen.findByRole('table', { name: 'Every dated entry' })
}

describe('News lag page', () => {
  it('heads the page with a stat strip, including the fastest entry', async () => {
    render(<NewsLagPage />)
    await loaded()

    expect(stat('Median news → order lag')).toContain('3.9 h')
    expect(stat('Median news → order lag')).toContain('0 of 21 entries within 1 h')
    expect(stat('Move gone before entry')).toContain('33%')
    expect(stat('Right way, option lost')).toContain('8 of 21')
    expect(stat('Measurable entries')).toContain('10 of 48')
    expect(stat('Fastest entry')).toContain('1.5 h')
    expect(stat('Fastest entry')).toContain('NVDA')
  })

  it('says in one line when publish prices fell back to the daily close', async () => {
    render(<NewsLagPage />)
    await loaded()

    expect(screen.getByText(/12 publish prices fall back to the prior daily close/).className).toContain('text-warning')
  })

  it('plots lag against option P/L, splits the move at entry, and counts right-way losers cell by cell', async () => {
    render(<NewsLagPage />)
    await loaded()

    expect(screen.getByRole('img', { name: /Median news-to-order lag 3.9 h/ })).toBeTruthy()
    expect(screen.getByRole('img', { name: '33% of the move came before entry, 67% after' })).toBeTruthy()
    const grid = screen.getByRole('img', { name: '8 of 21 closed trades went our way after entry but the option lost' })
    expect(grid.children).toHaveLength(21)
  })

  it('lists every dated entry by lag, each with its stock path bar', async () => {
    render(<NewsLagPage />)
    const table = await loaded()

    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent?.match(/^(NVDA|SKHY)/)?.[0])).toEqual(['NVDA', 'NVDA', 'SKHY'])
    expect(within(rows[1]).getByRole('img').getAttribute('aria-label')).toBe('NVDA: stock moved -1.44% before entry, +7.86% after entry (in the trade\'s direction).')
    expect(rows[2].textContent).toContain('Moved our way, option lost')
  })

  it('mutes lag buckets with fewer than five closed trades', async () => {
    render(<NewsLagPage />)
    await loaded()

    const rows = within(screen.getByRole('table', { name: 'Lag buckets' })).getAllByRole('row').slice(1)
    expect(rows.find((row) => row.textContent?.includes('1–6 hours'))!.className).not.toContain('opacity-60')
    expect(rows.find((row) => row.textContent?.includes('Overnight'))!.className).toContain('opacity-60')
  })

  it('asks for another window', async () => {
    render(<NewsLagPage />)
    await loaded()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Window' }), '30')
    await waitFor(() => expect(requests[requests.length - 1]).toContain('days=30'))
  })
})
