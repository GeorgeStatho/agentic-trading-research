import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import FollowerAccountsPage from './followerAccounts.tsx'

/** `/api/follower-accounts?days=0` on Nov 4, 2026: two mirrored entries, four followers. */
function trade(account: string, strategy: string, underlying: string, decision: string, order: string, closedAt: string, pnl: number, capital: number, production: number) {
  const primary = account === 'production'
  return {
    account, strategy, role: primary ? 'primary' : 'follower', underlying, decision, source_order_id: order,
    opened_at: primary ? null : '2026-10-29T15:21:55+00:00', closed_at: closedAt, exit_reason: primary ? null : 'production_exit',
    pnl, capital, pnl_pct: (100 * pnl) / capital, production_pnl_pct: production,
  }
}

const TSLA = ['TSLA', 'call', 'tsla-order', '2026-10-30T13:38:34+00:00'] as const
const ORCL = ['ORCL', 'put', 'orcl-order', '2026-11-03T14:41:10+00:00'] as const

function payload() {
  return {
    as_of: '2026-11-04T20:08:35+00:00',
    days: 0,
    accounts: {
      production: { strategy: 'single_leg', role: 'primary', closed: 2, active: 1, wins: 0, avg_pnl_pct: -24.2376, pnl: -620, capital: 1935, avg_pnl_per_exposure_pct: -2.46 },
      itm: { strategy: 'itm_single', role: 'follower', closed: 1, active: 0, failed: 0, skipped: { no_itm_strike: 1 }, wins: 0, pnl: -45, capital: 1320, avg_pnl_pct: -3.4091, production_avg_pnl_pct: -4.2373, production_pnl_on_capital_pct: -4.2373 },
      longer: { strategy: 'longer_dte', role: 'follower', closed: 2, active: 0, failed: 0, skipped: {}, wins: 0, pnl: -675, capital: 2260, avg_pnl_pct: -22.429, production_avg_pnl_pct: -24.2376, production_pnl_on_capital_pct: -32.0413, avg_pnl_per_exposure_pct: -1.93, production_avg_pnl_per_exposure_pct: -2.46 },
      shares: { strategy: 'shares', role: 'follower', closed: 1, active: 0, failed: 1, skipped: {}, wins: 0, pnl: -46.58, capital: 1218.48, avg_pnl_pct: -3.8228, production_avg_pnl_pct: -44.2379, production_pnl_on_capital_pct: -44.2379 },
      spreads: { strategy: 'debit_spread', role: 'follower', closed: 1, active: 0, failed: 0, skipped: { no_short_strike: 1 }, wins: 0, pnl: -31, capital: 378, avg_pnl_pct: -8.2011, production_avg_pnl_pct: -4.2373, production_pnl_on_capital_pct: -4.2373 },
    },
    trades: [
      trade('production', 'single_leg', ...TSLA, -595, 1345, -44.2379),
      trade('longer', 'longer_dte', ...TSLA, -645, 1600, -44.2379),
      trade('shares', 'shares', ...TSLA, -46.58, 1218.48, -44.2379),
      trade('production', 'single_leg', ...ORCL, -25, 590, -4.2373),
      trade('spreads', 'debit_spread', ...ORCL, -31, 378, -4.2373),
      trade('itm', 'itm_single', ...ORCL, -45, 1320, -4.2373),
      trade('longer', 'longer_dte', ...ORCL, -30, 660, -4.2373),
    ],
  }
}

let answer: unknown
const requests: string[] = []

beforeEach(() => {
  answer = payload()
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(String(input))
    return Response.json(answer)
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function stat(label: string): string {
  return screen.getByText(label, { selector: 'div' }).parentElement!.textContent ?? ''
}

async function loaded() {
  return screen.findByRole('table', { name: 'Per account' })
}

describe('Followers page', () => {
  it('heads the page with a stat strip', async () => {
    render(<FollowerAccountsPage />)
    await loaded()

    expect(stat('Follower accounts')).toContain('4')
    expect(stat('Follower accounts')).toContain('itm · longer · shares · spreads')
    expect(stat('Closed follower trades')).toContain('5')
    expect(stat('Closed follower trades')).toContain('across 2 production entries')
    expect(stat('Production, mirrored entries')).toContain('-24.2%')
    expect(stat('Best vs. production')).toContain('needs 5 closed trades in one account')
    expect(stat('Skipped / failed')).toContain('2 / 1')
  })

  it('groups each production entry with every vehicle that mirrored it', async () => {
    render(<FollowerAccountsPage />)
    await loaded()

    const chart = screen.getByRole('img', { name: /^Return by vehicle/ })
    expect(chart.getAttribute('aria-label')).toBe('Return by vehicle, same entry: TSLA call: production -44.2%, longer -40.3%, shares -3.8%; ORCL put: production -4.2%, itm -3.4%, longer -4.5%, spreads -8.2%')
  })

  it('charts each follower against production, best first, with its sample size', async () => {
    render(<FollowerAccountsPage />)
    await loaded()

    expect(screen.getByRole('img', { name: /^Difference vs. production/ }).getAttribute('aria-label'))
      .toBe('Difference vs. production: shares +40.4 pts (n=1), longer +1.8 pts (n=2), itm +0.8 pts (n=1), spreads -4.0 pts (n=1)')
  })

  it('compares each follower with production on capital as well as per trade', async () => {
    render(<FollowerAccountsPage />)
    const accounts = await loaded()

    const headers = within(accounts).getAllByRole('columnheader').map((cell) => cell.textContent)
    const cell = (account: string, header: string) => {
      const row = within(accounts).getAllByRole('row').find((candidate) => candidate.textContent?.startsWith(account))!
      return within(row).getAllByRole('cell')[headers.indexOf(header)].textContent
    }
    // longer: -$675 / $2,260 = -29.9% against production's -$620 / $1,935 = -32.0% on the same two entries.
    expect(cell('longer', 'Production P/L / capital')).toBe('-32.0%')
    expect(cell('longer', 'Capital diff')).toBe('+2.2 pts')
    expect(cell('longer', 'Avg diff')).toBe('+1.8 pts')
    expect(cell('production', 'P/L / capital')).toBe('-32.0%')
    expect(cell('production', 'Capital diff')).toBe('—')
    expect(cell('production', 'Win rate')).toBe('0%')
    expect(cell('production', 'Active')).toBe('1')
    expect(cell('production', 'Failed')).toBe('—')
  })

  it('shows each account\'s return per dollar of stock exposure, blank where delta was not recorded', async () => {
    render(<FollowerAccountsPage />)
    const accounts = await loaded()

    const headers = within(accounts).getAllByRole('columnheader').map((cell) => cell.textContent)
    const cell = (account: string) => {
      const row = within(accounts).getAllByRole('row').find((candidate) => candidate.textContent?.startsWith(account))!
      return within(row).getAllByRole('cell')[headers.indexOf('Avg per $ exposure')].textContent
    }
    expect(cell('longer')).toBe('-1.9%')
    expect(cell('production')).toBe('-2.5%')
    expect(cell('shares')).toBe('—')
  })

  it('mutes accounts under the sample guardrail and lists recent follower trades', async () => {
    render(<FollowerAccountsPage />)
    const accounts = await loaded()

    const rows = within(accounts).getAllByRole('row').slice(1)
    expect(rows[0].textContent).toContain('production')
    expect(rows.every((row) => row.className.includes('opacity-60'))).toBe(true)
    expect(within(screen.getByRole('table', { name: 'Recent closed follower trades' })).getAllByRole('row')).toHaveLength(6)
  })

  it('asks for another window', async () => {
    render(<FollowerAccountsPage />)
    await loaded()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Entries mirrored in' }), '30')
    await waitFor(() => expect(requests[requests.length - 1]).toContain('days=30'))
  })

  it('explains how to start when no follower is configured', async () => {
    answer = { as_of: '2026-11-04T20:08:35+00:00', days: 0, accounts: {}, trades: [] }
    render(<FollowerAccountsPage />)

    expect(await screen.findByText(/No follower positions in this window/)).toBeTruthy()
  })
})
