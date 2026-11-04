import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import BacktestPage from './backtest.tsx'

function run(id: number, overrides: Record<string, unknown> = {}, metrics: Record<string, unknown> = {}) {
  return {
    id, strategy_version: 'llm-replay-manager-v1', model: 'stored', prompt_version: 'stored-output',
    started_at: '2026-10-26T17:04:28+00:00', completed_at: '2026-10-26T17:05:00+00:00', status: 'completed', config: {},
    metrics: {
      expectancy_pct: 48.202, event_count: 35, filled_count: 3, no_fill_count: 32, portfolio_rejected_count: 7, snapshot_count: 35,
      fill_rate_pct: 8.57, win_rate_pct: 66.67, total_pnl_dollars: 448.275, max_drawdown_dollars: 203.825,
      equity_curve: [
        { at: '2026-09-29T20:00:00+00:00', pnl_dollars: -203.825 },
        { at: '2026-10-05T20:00:00+00:00', pnl_dollars: 448.275 },
      ],
      ...metrics,
    },
    ...overrides,
  }
}

function event(id: number, status: string) {
  return {
    id, as_of: '2026-09-22T14:00:00+00:00', symbol: 'NVDA', decision: 'call', option_symbol: 'NVDA261016C00180000', status,
    evidence_snapshot_id: 9, entry_at: '', exit_at: '', entry_price: 4.1, exit_price: 6.2, quantity: 1,
    realized_pnl_pct: status === 'simulated_closed' ? 51.2 : null, realized_pnl_dollars: null, commission_dollars: 0.65,
    quote_provider: 'marketdata', pricing_source: 'historical_option_quotes', warnings: [],
  }
}

const RUNS = [
  run(140),
  run(139, { model: 'deepseek/deepseek-v4.1-flash-1017:floor' }, { expectancy_pct: null, event_count: 0, filled_count: 0, no_fill_count: 0, portfolio_rejected_count: 0, snapshot_count: 0, equity_curve: [] }),
  run(138, {}, { expectancy_pct: 51.116 }),
]

const EVENTS = [
  ...[1, 2, 3].map((id) => event(id, 'simulated_closed')),
  ...Array.from({ length: 7 }, (_, index) => event(10 + index, 'portfolio_rejected')),
  ...Array.from({ length: 25 }, (_, index) => event(100 + index, 'no_fill')),
]

const calls: { url: string; method: string }[] = []

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, method: init?.method ?? 'GET' })
    if (url.startsWith('/api/backtests?')) return Response.json({ runs: RUNS })
    const id = Number(url.split('/').pop())
    return Response.json({ run: RUNS.find((entry) => entry.id === id), events: id === 140 ? EVENTS : [], read_only: true })
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function loaded() {
  return screen.findByRole('img', { name: /Where the 35 decisions went/ })
}

function stat(label: string): string {
  return screen.getByText(label, { selector: 'div' }).parentElement!.textContent ?? ''
}

describe('Backtests page', () => {
  it('lists run history and opens the newest run', async () => {
    render(<BacktestPage />)
    await loaded()

    const history = screen.getByRole('table', { name: 'Run history' })
    const run140 = within(history).getByRole('button', { name: '#140' })
    expect(run140.getAttribute('aria-pressed')).toBe('true')
    expect(run140.closest('tr')!.className).toContain('bg-raised')
    expect(history.textContent).toContain('deepseek/deepseek-v4.1-flash-1017:floor')
  })

  it('heads the run with a stat strip', async () => {
    render(<BacktestPage />)
    await loaded()

    expect(stat('Filled')).toContain('3')
    expect(stat('Expectancy')).toContain('+48.20%')
    expect(stat('Total P/L')).toContain('$448.28')
    expect(stat('Max drawdown')).toContain('$203.83')
    expect(stat('Examined')).toContain('35')
  })

  it('charts where the decisions went, the cumulative P/L, and expectancy across runs', async () => {
    render(<BacktestPage />)

    expect((await loaded()).getAttribute('aria-label')).toBe('Where the 35 decisions went: 3 filled, 7 guardrail rejects, 25 no fill')
    expect(screen.getByRole('img', { name: 'Simulated cumulative P/L +$448.28 after 2 closes' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Expectancy by run: #138 +51.12%, #139 no fills, #140 +48.20%' })).toBeTruthy()
  })

  it('opens another run from the history', async () => {
    render(<BacktestPage />)
    await loaded()

    await userEvent.click(within(screen.getByRole('table', { name: 'Run history' })).getByRole('button', { name: '#138' }))
    await waitFor(() => expect(calls.some((call) => call.url === '/api/backtests/138')).toBe(true))
    await waitFor(() => expect(screen.getByRole('button', { name: '#138' }).getAttribute('aria-pressed')).toBe('true'))
  })

  it('never runs a backtest or fetches quotes on its own', async () => {
    render(<BacktestPage />)
    await loaded()

    expect(calls.filter((call) => call.method !== 'GET')).toEqual([])
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Fetch next EOD batch' })).toBeTruthy()
  })

  it('keeps the run settings as compact inputs', async () => {
    render(<BacktestPage />)
    await loaded()

    const settings = screen.getByRole('group', { name: 'Backtest settings' })
    expect((within(settings).getByRole('spinbutton', { name: 'Entry slippage (bps)' }) as HTMLInputElement).value).toBe('25')
    expect(within(settings).getByRole('spinbutton', { name: 'Daily loss limit ($)' }).className).toContain('input-xs')
  })

  it('says why some decisions have no outcome, in one line', async () => {
    render(<BacktestPage />)
    await loaded()

    expect(screen.getByText(/Some decisions have no simulated outcome/).className).toContain('text-warning')
  })
})
