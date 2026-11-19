import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.tsx'
import { installSnapshotFetch, NOT_CAPTURED, snapshotKey } from './snapshot.ts'
import { SnapshotBanner } from './snapshotBanner.tsx'
import keyExamples from './test/contracts/snapshot-keys.json'
import { CsvLink } from './ui/CsvLink.tsx'

/** Serves the snapshot's files under `/site/data/`; anything else is missing, as on GitHub Pages. */
function stubPages(files: Record<string, unknown>) {
  const served: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    served.push(String(input))
    const body = files[String(input)]
    return body === undefined ? new Response('Not Found', { status: 404 }) : Response.json(body)
  }))
  return served
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('snapshotKey', () => {
  it('names files exactly as the capture script does', () => {
    for (const [url, key] of Object.entries(keyExamples)) expect(snapshotKey(url), url).toBe(key)
  })
})

describe('installSnapshotFetch', () => {
  it('reads API requests from the saved files, ignoring the cache-busting stamp', async () => {
    const served = stubPages({ '/site/data/dashboard-kpis.json': { ok: true } })
    installSnapshotFetch('/site/')

    const response = await fetch('/api/dashboard-kpis?ts=1763500000000')
    expect(await response.json()).toEqual({ ok: true })
    expect(served).toEqual(['/site/data/dashboard-kpis.json'])
  })

  it('answers a view that was not captured with 404 and says why', async () => {
    stubPages({})
    installSnapshotFetch('/site/')

    const response = await fetch('/api/decision-expectancy?basis=realized')
    expect(response.status).toBe(404)
    expect(await response.json()).toEqual({ error: NOT_CAPTURED })
  })

  it('leaves other requests alone', async () => {
    const served = stubPages({ '/site/data/manifest.json': { captured_at: 'x' } })
    installSnapshotFetch('/site/')

    await fetch('/site/data/manifest.json')
    expect(served).toEqual(['/site/data/manifest.json'])
  })
})

describe('snapshot mode', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_SNAPSHOT', '1')
    vi.stubEnv('BASE_URL', '/site/')
  })

  it('says when the snapshot was taken', async () => {
    stubPages({ '/site/data/manifest.json': { captured_at: '2026-11-19T22:30:00+00:00' } })
    render(<SnapshotBanner />)

    const banner = await screen.findByRole('status', { name: 'Snapshot' })
    expect(banner.textContent).toMatch(/Daily snapshot taken .*2026/)
    expect(banner.textContent).toContain('only the default views')
  })

  it('shows the snapshot banner instead of the live status line', async () => {
    stubPages({ '/site/data/manifest.json': { captured_at: '2026-11-19T22:30:00+00:00' } })
    window.location.hash = '#/live'
    render(<App />)

    expect(await screen.findByRole('status', { name: 'Snapshot' })).toBeTruthy()
    expect(screen.queryByRole('status', { name: 'System status' })).toBeNull()
  })

  it('offers no CSV downloads', () => {
    const { container } = render(<CsvLink href="/api/executed-trades?format=csv" />)
    expect(container.innerHTML).toBe('')
  })
})
