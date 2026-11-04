import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import NewsExtractionPage from './newsExtraction.tsx'
import { RiskControlsView } from './riskControls.tsx'

function source(name: string, attempted: number, succeeded: number, permanent = 0, topError: string | null = null) {
  return { source: name, attempted, succeeded, failed: attempted - succeeded, permanent_failures: permanent, success_rate: attempted ? succeeded / attempted : 0, top_error: topError }
}

const requests: string[] = []

beforeEach(() => {
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(String(input))
    return Response.json({
      days: 7, start: '2026-10-29', end: '2026-11-04',
      sources: [source('finance.yahoo.com', 100, 100), source('wsj.com', 20, 4, 16, 'HTTP 403'), source('tiny.example', 2, 0)],
    })
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function stat(label: string): string {
  return screen.getByText(label, { selector: 'div' }).parentElement!.textContent ?? ''
}

describe('System › Extraction', () => {
  it('heads the page with a stat strip', async () => {
    render(<NewsExtractionPage />)
    await screen.findByRole('table', { name: 'Per source' })

    expect(stat('Overall success rate')).toContain('85%')
    expect(stat('Overall success rate')).toContain('104 of 122 urls saved')
    expect(stat('Sources below 70%')).toContain('1 of 2')
    expect(stat('Blocked urls')).toContain('16')
  })

  it('flags sources under the pass mark and mutes the ones too small to judge', async () => {
    render(<NewsExtractionPage />)
    const table = await screen.findByRole('table', { name: 'Per source' })

    const rows = within(table).getAllByRole('row').slice(1)
    const wsj = rows.find((row) => row.textContent?.includes('wsj.com'))!
    expect(wsj.textContent).toContain('Below pass mark')
    expect(within(wsj).getByRole('img').getAttribute('aria-label')).toBe('wsj.com: 20% of 20 urls extracted (4 saved, 16 failed).')
    expect(rows.find((row) => row.textContent?.includes('tiny.example'))!.className).toContain('opacity-60')
  })

  it('asks for another window', async () => {
    render(<NewsExtractionPage />)
    await screen.findByRole('table', { name: 'Per source' })

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Window' }), '30')
    await waitFor(() => expect(requests[requests.length - 1]).toContain('days=30'))
  })
})

describe('System › Risk settings', () => {
  it('lists every rule open in a table, marking the ones not configured', () => {
    render(<RiskControlsView controls={[
      { label: 'Premium cap per trade', value: '$1,500', detail: 'Hard ceiling on one entry.', status: 'configured' },
      { label: 'Daily loss limit', value: 'unset', detail: 'Stops new entries after a bad day.', status: 'missing' },
    ]} />)

    const table = screen.getByRole('table', { name: 'Risk settings' })
    expect(table.closest('details')).toBeNull()
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows[0].textContent).toContain('Premium cap per trade')
    expect(rows[1].textContent).toContain('Not configured')
    expect(screen.getByRole('region', { name: /Risk settings/ }).textContent).toContain('1 not configured')
  })
})
