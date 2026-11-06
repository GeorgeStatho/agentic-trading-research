import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AttributionPage from './attribution.tsx'
import { decisionAttributionPayload, exitAttributionPayload } from './test/reportFixtures.ts'

const requests: string[] = []

beforeEach(() => {
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input)
    requests.push(url)
    return Response.json(url.includes('exit-attribution') ? exitAttributionPayload() : decisionAttributionPayload())
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function rowFor(table: HTMLElement, text: string): string {
  return within(table).getAllByRole('row').find((row) => row.textContent?.includes(text))?.textContent ?? ''
}

describe('Research › Attribution', () => {
  it('shows how much profit each exit reason gave back from the best price', async () => {
    render(<AttributionPage />)
    const table = await screen.findByRole('table', { name: 'Exit attribution' })
    expect(rowFor(table, 'stop loss')).toContain('43.7')
    expect(rowFor(table, 'stop loss')).toContain('10')
  })

  it('shows hit rate by the model’s own rating and by evidence source', async () => {
    render(<AttributionPage />)
    const ratings = await screen.findByRole('table', { name: 'By model self-rating' })
    expect(rowFor(ratings, 'high')).toContain('55%')
    expect(rowFor(ratings, 'high')).toContain('±3%')
    const sources = screen.getByRole('table', { name: 'By evidence source' })
    expect(rowFor(sources, 'grounding proxy')).toContain('51%')
    const quality = screen.getByRole('table', { name: 'By evidence quality' })
    expect(rowFor(quality, 'adequate')).toContain('49%')
  })

  it('asks for the 10-day horizon when it is picked', async () => {
    render(<AttributionPage />)
    await screen.findByRole('table', { name: 'By model self-rating' })
    await userEvent.click(screen.getByRole('button', { name: '10 days' }))
    expect(requests.some((url) => url.includes('/api/decision-attribution?days=90&horizon=10'))).toBe(true)
  })
})
