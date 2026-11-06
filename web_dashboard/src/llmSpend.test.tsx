import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LlmSpendPanel } from './llmSpend.tsx'
import { llmUsagePayload } from './test/reportFixtures.ts'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(llmUsagePayload())))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function stat(label: string): string {
  return screen.getByText(label, { selector: 'div' }).parentElement!.textContent ?? ''
}

describe('LLM spend', () => {
  it('heads with spend over 7 and 30 days and the cost per call', async () => {
    render(<LlmSpendPanel />)
    await screen.findByRole('table', { name: 'Spend by stage' })

    expect(stat('Last 30 days')).toContain('$8.55')
    expect(stat('Last 30 days')).toContain('1,214 calls')
    expect(stat('Last 7 days')).toContain('$2.10')
    expect(stat('Per call')).toContain('$0.007')
  })

  it('splits spend by stage, provider and day', async () => {
    render(<LlmSpendPanel />)
    const stages = await screen.findByRole('table', { name: 'Spend by stage' })
    const rows = within(stages).getAllByRole('row').slice(1).map((row) => row.textContent ?? '')
    expect(rows[0]).toContain('Unknown')
    expect(rows[1]).toContain('manager')
    expect(rows[1]).toContain('$0.60')
    expect(within(screen.getByRole('table', { name: 'Spend by provider' })).getByText('Wafer')).toBeTruthy()
    expect(within(screen.getByRole('table', { name: 'Spend by day' })).getAllByRole('row')).toHaveLength(3)
  })
})
