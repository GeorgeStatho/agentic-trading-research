import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExecutionQualityPanel } from './executionQuality.tsx'
import { executionQualityPayload } from './test/reportFixtures.ts'

const requests: string[] = []

beforeEach(() => {
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(String(input))
    return Response.json(executionQualityPayload())
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Execution quality panel', () => {
  it('shows fill rate, slippage and spread paid per account and side', async () => {
    render(<ExecutionQualityPanel />)
    const table = await screen.findByRole('table', { name: 'Execution quality' })
    const [row] = within(table).getAllByRole('row').slice(1)

    expect(row.textContent).toContain('Paper')
    expect(row.textContent).toContain('Buy')
    expect(row.textContent).toContain('50%')
    expect(row.textContent).toContain('$0.04')
    expect(row.textContent).toContain('6.2%')
    expect(requests[0]).toContain('/api/execution-quality?days=30')
  })
})
