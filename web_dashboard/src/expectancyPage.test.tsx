import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DecisionExpectancyPage from './decisionExpectancy.tsx'
import type { ExpectancyPayload } from './expectancy.ts'
import { expectancyPayload } from './test/expectancyFixture.ts'

let answer: ExpectancyPayload
const requests: URLSearchParams[] = []

beforeEach(() => {
  answer = expectancyPayload()
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(new URL(String(input), 'http://localhost').searchParams)
    return Response.json(answer)
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function firstCells(table: HTMLElement): string[] {
  return within(table).getAllByRole('row').slice(1).map((row) => (row as HTMLTableRowElement).cells[0].textContent ?? '')
}

async function loaded() {
  return screen.findByRole('table')
}

describe('Expectancy page', () => {
  it('opens with a stat strip, expectancy muted and sized while under the guardrail', async () => {
    render(<DecisionExpectancyPage />)
    await loaded()

    const expectancy = screen.getByText('Expectancy', { selector: 'div' }).parentElement!
    expect(expectancy.textContent).toContain('-25.17%')
    expect(expectancy.textContent).toContain('n=2')
    expect(expectancy.querySelector('.text-dim')).toBeTruthy()
    expect(screen.getByText('Win rate', { selector: 'div' }).parentElement!.textContent).toContain('0 / 2')
    expect(screen.getByText('Eligible trades', { selector: 'div' }).parentElement!.textContent).toContain('16')
  })

  it('says in one line that marks are not realized and how many cohorts are under the guardrail', async () => {
    render(<DecisionExpectancyPage />)
    await loaded()

    expect(screen.getByText(/Marked P\/L, not realized.*2 of 2 cohorts are below the 5-sample guardrail/)).toBeTruthy()
  })

  it('charts expectancy per cohort, stating it for screen readers', async () => {
    render(<DecisionExpectancyPage />)
    await loaded()

    expect(screen.getByRole('img', { name: 'Expectancy by cohort: Medium -25.17% (n=2), High no samples yet' })).toBeTruthy()
  })

  it('meters each cohort toward the sample guardrail', async () => {
    render(<DecisionExpectancyPage />)
    await loaded()

    const medium = screen.getByRole('progressbar', { name: 'Medium' }) as HTMLProgressElement
    expect(medium.value).toBe(2)
    expect(medium.max).toBe(5)
  })

  it('splits eligible trades into resolved and unresolved', async () => {
    render(<DecisionExpectancyPage />)
    await loaded()

    expect(screen.getByRole('img', { name: '2 of 16 eligible trades resolved, 14 unresolved' })).toBeTruthy()
  })

  it('lists cohorts in a table, muting those under the guardrail, and sorts by a header', async () => {
    answer = expectancyPayload({ cohorts: [...expectancyPayload().cohorts.slice(0, 1).map((row) => ({ ...row, insufficient_sample: false })), expectancyPayload().cohorts[1]] })
    render(<DecisionExpectancyPage />)
    const table = await loaded()

    const rows = within(table).getAllByRole('row').slice(1)
    expect(firstCells(table)).toEqual([expect.stringContaining('Medium'), expect.stringContaining('High')])
    expect(rows[0].className).not.toContain('opacity-60')
    expect(rows[1].className).toContain('opacity-60')

    await userEvent.click(within(table).getByRole('button', { name: /Cohort/ }))
    expect(firstCells(table)).toEqual([expect.stringContaining('High'), expect.stringContaining('Medium')])
  })

  it('asks the API again when a filter changes', async () => {
    render(<DecisionExpectancyPage />)
    await loaded()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Group by' }), 'sector')
    await waitFor(() => expect(requests[requests.length - 1].get('group_by')).toBe('sector'))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Window' }), '30')
    await waitFor(() => expect(requests[requests.length - 1].get('within_days')).toBe('30'))
  })

  it('offers ticker, industry, exit reason, catalyst and holding period groupings', async () => {
    render(<DecisionExpectancyPage />)
    await loaded()

    const groupBy = screen.getByRole('combobox', { name: 'Group by' })
    for (const value of ['ticker', 'industry', 'exit_reason', 'catalyst_family', 'holding_period']) {
      await userEvent.selectOptions(groupBy, value)
      await waitFor(() => expect(requests[requests.length - 1].get('group_by')).toBe(value))
    }
  })

  it('downloads the cohorts on screen as CSV, with the current filters', async () => {
    render(<DecisionExpectancyPage />)
    await loaded()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Group by' }), 'ticker')

    const href = new URL(screen.getByRole('link', { name: 'Download CSV' }).getAttribute('href')!, 'http://x')
    expect(href.pathname).toBe('/api/decision-expectancy')
    expect(href.searchParams.get('group_by')).toBe('ticker')
    expect(href.searchParams.get('format')).toBe('csv')
  })

  it('shows the error the API gives', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: 'basis must be marked or closed' }, { status: 400 })))
    render(<DecisionExpectancyPage />)

    expect(await screen.findByText('basis must be marked or closed')).toBeTruthy()
  })
})
