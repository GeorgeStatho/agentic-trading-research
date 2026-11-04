import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.tsx'

const KPIS = {
  bot_status: {
    state: 'healthy',
    label: 'Healthy',
    detail: 'News Collector running, Worker paused',
    components: [
      { name: 'News Collector', state: 'running', recent_error: false },
      { name: 'Worker', state: 'paused', recent_error: false },
    ],
  },
  market_status: { state: 'closed', label: 'Closed', detail: '' },
}

const SCRIPT_STATUS = { state: 'paused', message: 'Market is closed', pid: 1, updated_at: '2026-11-04T15:38:00' }

/** The shell's status endpoints answer; every page endpoint fails, so pages render their error states. */
function stubApi() {
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input)
    if (url.startsWith('/api/dashboard-kpis')) return Response.json(KPIS)
    if (url.startsWith('/api/script-status')) return Response.json(SCRIPT_STATUS)
    return new Response('unavailable', { status: 503 })
  }))
}

function renderAt(hash: string) {
  window.location.hash = hash
  return render(<App />)
}

describe('App shell', () => {
  beforeEach(stubApi)
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('marks the current page in the top nav and offers demo mode', () => {
    renderAt('#/research/expectancy')

    const nav = screen.getByRole('navigation', { name: 'Dashboard pages' })
    expect(within(nav).getByRole('link', { name: 'Research' }).getAttribute('aria-current')).toBe('page')
    expect(within(nav).getByRole('link', { name: 'Live' }).getAttribute('aria-current')).toBeNull()
    expect(within(nav).getByRole('button', { name: 'Demo Mode' })).toBeTruthy()
  })

  it('drops the placeholder icon buttons that do nothing', () => {
    renderAt('#/research/expectancy')

    for (const name of ['Notifications', 'Settings', 'Account']) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
  })

  it('labels the account as paper trading and shows the market state', async () => {
    renderAt('#/research/expectancy')

    // Pages render their own <header>, so find the top bar through its nav.
    const topBar = screen.getByRole('navigation', { name: 'Dashboard pages' }).closest('header') as HTMLElement
    expect(within(topBar).getByText('PAPER')).toBeTruthy()
    expect(await within(topBar).findByText('Market closed')).toBeTruthy()
  })

  it('shows each bot component and the error count in the status bar', async () => {
    renderAt('#/research/expectancy')

    const status = screen.getByRole('status', { name: 'System status' })
    expect(await within(status).findByText('News collector running')).toBeTruthy()
    expect(within(status).getByText('Worker paused')).toBeTruthy()
    expect(within(status).getByText('No errors')).toBeTruthy()
    expect(await within(status).findByText(/^Last run /)).toBeTruthy()
  })

  it('has no section sidebar on the Live page', () => {
    renderAt('#/live')
    expect(screen.queryByRole('complementary', { name: 'Page sections' })).toBeNull()
  })

  it('links each view of a page in the sub-tabs, marking the current one', () => {
    renderAt('#/research/expectancy')

    const tabs = screen.getByRole('navigation', { name: 'Research views' })
    expect(within(tabs).getByRole('link', { name: 'Expectancy' }).getAttribute('aria-current')).toBe('page')
    expect(within(tabs).getByRole('link', { name: 'News lag' }).getAttribute('href')).toBe('#/research/news-lag')
  })
})
