import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ExposureMapPage from './exposureMap.tsx'

const ARTICLES = [
  { id: 13449, title: 'Intel stock plunges 20% on weak guidance', summary: '', source: 'newswire.example.com', source_url: 'https://newswire.example.com/intel', published_at: '2026-10-29T20:04:08+0000', evidence_score: 0.8124 },
  { id: 12748, title: 'Waymo remains a niche, cash-burning business', summary: '', source: 'newswire.example.com', source_url: '', published_at: '2026-10-28T23:53:58+0000', evidence_score: 0.8056 },
]

function notAnalyzed(articleId: number) {
  return {
    article_id: articleId,
    has_run: false,
    article: ARTICLES.find((article) => article.id === articleId),
    nodes: [],
    edges: [],
    position_impacts: [],
    uncertainties: [],
    no_trade_executed: true,
  }
}

const calls: { url: string; method: string }[] = []

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, method: init?.method ?? 'GET' })
    if (url.startsWith('/api/exposure-map/articles')) return Response.json({ articles: ARTICLES })
    const id = Number(url.split('/').pop())
    return Response.json(notAnalyzed(id))
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Exposure map', () => {
  it('lists recent articles with their evidence score and opens the first', async () => {
    render(<ExposureMapPage />)

    const queue = await screen.findByRole('list', { name: 'Recent news' })
    const items = within(queue).getAllByRole('button')
    expect(items).toHaveLength(2)
    expect(items[0].textContent).toContain('Evidence 0.81')
    expect(items[0].getAttribute('aria-pressed')).toBe('true')
    await waitFor(() => expect(calls.some((call) => call.url === '/api/exposure-map/13449')).toBe(true))
  })

  it('shows the prompt instead of an empty graph, filters or legend until the article is analyzed', async () => {
    const { container } = render(<ExposureMapPage />)

    expect(await screen.findByText(/Select “Analyze article”/)).toBeTruthy()
    expect(container.querySelector('.react-flow')).toBeNull()
    expect(screen.queryByRole('group', { name: 'Impact' })).toBeNull()
    expect(screen.queryByText('Positive impact')).toBeNull()
  })

  it('loads the map for the article picked', async () => {
    render(<ExposureMapPage />)
    const queue = await screen.findByRole('list', { name: 'Recent news' })

    await userEvent.click(within(queue).getAllByRole('button')[1])
    await waitFor(() => expect(calls.some((call) => call.url === '/api/exposure-map/12748')).toBe(true))
    expect(screen.getByRole('heading', { level: 2, name: 'Waymo remains a niche, cash-burning business' })).toBeTruthy()
  })

  it('never starts a paid analysis on its own', async () => {
    render(<ExposureMapPage />)
    await screen.findByText(/Select “Analyze article”/)

    expect(calls.filter((call) => call.method !== 'GET')).toEqual([])
    expect(screen.getByRole('button', { name: 'Analyze article' })).toBeTruthy()
  })

  it('says no trade is executed by this feature', async () => {
    render(<ExposureMapPage />)
    expect(await screen.findByText('No trade is executed by this feature.')).toBeTruthy()
  })
})
