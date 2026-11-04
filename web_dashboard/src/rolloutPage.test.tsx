import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { forwardPayload, v2Payload } from './test/rolloutFixture.ts'
import V2StatusPage from './v2Status.tsx'

const requests: string[] = []

beforeEach(() => {
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input)
    requests.push(url)
    if (url.startsWith('/api/v2-status')) return Response.json(v2Payload())
    if (url.startsWith('/api/demo-mode')) return Response.json({ forward_evaluation: { ...forwardPayload(), demo_data: true } })
    return Response.json(forwardPayload())
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function stat(label: string): string {
  return screen.getByText(label, { selector: 'div' }).parentElement!.textContent ?? ''
}

async function loaded() {
  return screen.findByRole('list', { name: 'Would-block rate by control' })
}

describe('V2 rollout page', () => {
  it('leads with the mode counts as a stat strip', async () => {
    render(<V2StatusPage />)
    await loaded()

    expect(stat('Enforcing')).toContain('0')
    expect(stat('Enforcing')).toContain('of 6 controls')
    expect(stat('Shadow')).toContain('3 requested enforce')
    expect(stat('Downgraded')).toContain('3')
    expect(screen.getByText(/3 controls are set to enforce but running in shadow/).className).toContain('text-warning')
  })

  it('charts each control’s would-block rate, accent only where enforce was requested', async () => {
    render(<V2StatusPage />)
    const chart = await loaded()

    const bars = within(chart).getAllByRole('listitem')
    const provenance = bars.find((bar) => bar.textContent?.includes('Provenance'))!
    expect(provenance.textContent).toContain('100%')
    expect(provenance.textContent).toContain('2,577 / 2,577')
    expect(provenance.querySelector('.bg-primary')).toBeTruthy()
    const continuation = bars.find((bar) => bar.textContent?.includes('Continuation'))!
    expect(continuation.textContent).toContain('0%')
    expect(continuation.querySelector('.bg-primary')).toBeNull()
    expect(bars.some((bar) => bar.textContent?.includes('Execution liquidity'))).toBe(true)
  })

  it('warns when a requested-enforce control would block nearly everything', async () => {
    render(<V2StatusPage />)
    await loaded()

    expect(screen.getByText(/Provenance and Regime would block at least 95% of the candidates they see/)).toBeTruthy()
  })

  it('meters prerequisites that carry a count', async () => {
    render(<V2StatusPage />)
    await loaded()

    const meter = screen.getByRole('progressbar', { name: 'Outcome resolution' }) as HTMLProgressElement
    expect(meter.value).toBe(31)
    expect(meter.max).toBe(37)
    expect(screen.getByText('2577 in 30d')).toBeTruthy()
  })

  it('tables requested against effective mode', async () => {
    render(<V2StatusPage />)
    await loaded()

    const rows = within(screen.getByRole('table', { name: 'Controls' })).getAllByRole('row').slice(1)
    const provenance = rows.find((row) => row.textContent?.includes('Provenance'))!
    expect(provenance.textContent).toContain('Enforce')
    expect(provenance.textContent).toContain('Shadow')
    expect(provenance.textContent).toContain('no proof')
    expect(rows.find((row) => row.textContent?.includes('Scenario selection'))!.textContent).toContain('2 different contract')
  })

  it('shows forward cohorts with enrollment meters and the discovery ledger as stacked bars', async () => {
    render(<V2StatusPage />)
    await screen.findByRole('table', { name: 'Forward cohorts' })

    expect(screen.getAllByRole('progressbar', { name: 'production enrolled' })).toHaveLength(2)
    expect(screen.getByRole('img', { name: 'production route: 157 admitted, 1,001 not admitted, 4,788 not evaluated' })).toBeTruthy()
  })

  it('in demo mode shows only the forward-evaluation walkthrough', async () => {
    render(<V2StatusPage demoMode />)
    await screen.findByRole('table', { name: 'Forward cohorts' })

    expect(requests.some((url) => url.startsWith('/api/v2-status'))).toBe(false)
    expect(screen.getByText(/Seeded fictional walkthrough/)).toBeTruthy()
  })
})
