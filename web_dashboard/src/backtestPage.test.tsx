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

const REPLAY = {
  source_decision_count: 40, missing_frozen_input_count: 2, excluded_pre_knowledge_cutoff: 5, spaced_out_decision_count: 0,
  min_hours_between_same_symbol: 0, outcomes: { traded: 35, hold: 5 }, changed_direction_count: 4, cached_answer_count: 38,
  provider: 'openrouter', sample: 1, strategist_replay: null, thesis_recheck: { rechecked: 12, changed: 3 },
  production_settings: { overrides: [], selector_settings_differing_from_local: [] },
  daily_bars: { fetched: [], failed: {}, still_missing: [] },
}

const RUNS = [
  run(140, { config: { max_spread_pct: 0.25, max_entry_delay_minutes: 1440 } }, {
    replay: REPLAY, median_pnl_pct: 40.5, profit_factor_like: 2.75,
    before_portfolio_limits: { priced_entry_count: 9, symbol_count: 4, expectancy_pct: 30.25, median_pnl_pct: 20, win_rate_pct: 55.56 },
  }),
  run(139, { model: 'deepseek/deepseek-v4.1-flash-1017:floor' }, { expectancy_pct: null, event_count: 0, filled_count: 0, no_fill_count: 0, portfolio_rejected_count: 0, snapshot_count: 0, equity_curve: [] }),
  run(138, { strategy_version: 'frozen-manager-decision-v1', model: 'stored-manager-decision' }, { expectancy_pct: 51.116, filled_count: 6 }),
]

const EVENTS = [
  ...[1, 2, 3].map((id) => event(id, 'simulated_closed')),
  ...Array.from({ length: 7 }, (_, index) => event(10 + index, 'portfolio_rejected')),
  ...Array.from({ length: 25 }, (_, index) => event(100 + index, 'no_fill')),
]

const calls: { url: string; method: string; body?: string }[] = []

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body as string | undefined })
    if (url.startsWith('/api/backtests?')) return Response.json({ runs: RUNS })
    if (url === '/api/backtests' && init?.method === 'POST') return Response.json({ run: RUNS[0], events: EVENTS, read_only: true })
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

function historyIds(): string[] {
  return within(screen.getByRole('table', { name: 'Run history' })).getAllByRole('button', { name: /^#\d+$/ }).map((button) => button.textContent ?? '')
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

  it('loads up to 100 runs', async () => {
    render(<BacktestPage />)
    await loaded()

    expect(calls[0].url).toContain('limit=100')
  })

  it('filters run history by kind', async () => {
    render(<BacktestPage />)
    await loaded()
    const history = screen.getByRole('table', { name: 'Run history' })

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Kind' }), 'llm')
    expect(within(history).queryByRole('button', { name: '#138' })).toBeNull()
    expect(within(history).getByRole('button', { name: '#140' })).toBeTruthy()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Kind' }), 'stored')
    expect(historyIds()).toEqual(['#138'])
  })

  it('filters run history by model', async () => {
    render(<BacktestPage />)
    await loaded()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Model' }), 'deepseek/deepseek-v4.1-flash-1017:floor')
    expect(historyIds()).toEqual(['#139'])
  })

  it('sorts run history by expectancy, runs without fills last', async () => {
    render(<BacktestPage />)
    await loaded()
    const history = screen.getByRole('table', { name: 'Run history' })

    await userEvent.click(within(history).getByRole('button', { name: /^Expectancy/ }))
    expect(historyIds()).toEqual(['#138', '#140', '#139'])
    await userEvent.click(within(history).getByRole('button', { name: /^Expectancy/ }))
    expect(historyIds()).toEqual(['#140', '#138', '#139'])
  })

  it('shows each run\'s filled count', async () => {
    render(<BacktestPage />)
    await loaded()
    const history = screen.getByRole('table', { name: 'Run history' })

    expect(within(history).getByRole('button', { name: /^n/ })).toBeTruthy()
    const run138 = within(history).getByRole('button', { name: '#138' }).closest('tr')!
    expect(run138.textContent).toContain('6')
  })

  it('explains an LLM replay with a replay summary', async () => {
    render(<BacktestPage />)
    await loaded()

    const summary = screen.getByRole('region', { name: 'Replay summary' })
    expect(summary.textContent).toMatch(/40\s*decisions replayed/)
    expect(summary.textContent).toMatch(/5\s*excluded by knowledge cutoff/)
    expect(summary.textContent).toMatch(/sample 1/i)
    expect(summary.textContent).toMatch(/3 of 12/)

    await userEvent.click(within(screen.getByRole('table', { name: 'Run history' })).getByRole('button', { name: '#138' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '#138' }).getAttribute('aria-pressed')).toBe('true'))
    expect(screen.queryByRole('region', { name: 'Replay summary' })).toBeNull()
  })

  it("shows the run's resolved settings", async () => {
    render(<BacktestPage />)
    await loaded()

    const settings = screen.getByRole('region', { name: 'Run settings' }).textContent ?? ''
    expect(settings).toMatch(/Max spread.*0\.25/)
    expect(settings).toMatch(/Max entry delay.*1440/)
  })

  it('surfaces median, profit factor and the pre-limit expectancy', async () => {
    render(<BacktestPage />)
    await loaded()

    expect(stat('Expectancy')).toContain('median +40.50%')
    expect(stat('Total P/L')).toContain('profit factor 2.75')
    expect(stat('Examined')).toContain('before limits: 9 priced · +30.25%')
  })

  it('sends a date range', async () => {
    render(<BacktestPage />)
    await loaded()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Decision window' }), 'range')
    await userEvent.type(screen.getByLabelText('From'), '2026-10-01')
    await userEvent.type(screen.getByLabelText('To'), '2026-10-31')
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))

    await waitFor(() => expect(calls.some((call) => call.method === 'POST')).toBe(true))
    const body = JSON.parse(calls.find((call) => call.method === 'POST')!.body!)
    expect(body.limit).toBeNull()
    expect(body.start_at).toBe(new Date('2026-10-01T00:00').toISOString())
    expect(body.end_at).toBe(new Date('2026-10-31T23:59:59.999').toISOString())
  })

  it('sends quote and entry settings', async () => {
    render(<BacktestPage />)
    await loaded()

    const settings = screen.getByRole('group', { name: 'Backtest settings' })
    const spread = within(settings).getByRole('spinbutton', { name: 'Max spread (ratio)' })
    await userEvent.clear(spread)
    await userEvent.type(spread, '0.4')
    const qty = within(settings).getByRole('spinbutton', { name: 'Order qty' })
    await userEvent.clear(qty)
    await userEvent.type(qty, '2')
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))

    await waitFor(() => expect(calls.some((call) => call.method === 'POST')).toBe(true))
    const { config } = JSON.parse(calls.find((call) => call.method === 'POST')!.body!)
    expect(config).toMatchObject({ max_spread_pct: 0.4, default_order_qty: 2, min_volume: 0, max_entry_delay_minutes: 1440 })
  })
})
