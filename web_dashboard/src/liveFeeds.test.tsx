import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { decisionRows, decisionSummary, newsRows, visibleRows } from './liveFeeds.ts'
import type { CompanyDecisionsFeed, CompanyNewsFeed } from './liveFeeds.ts'
import { LiveDecisionsView } from './liveDecisions.tsx'
import { LiveNewsfeedView } from './liveNewsfeed.tsx'

function article(id: number, published_at: string, title: string, source = 'reuters.com', confidence?: string) {
  return {
    article_id: id,
    published_at,
    title,
    source,
    source_url: `https://${source}/${id}`,
    assessments: confidence ? [{ confidence }] : [],
  }
}

const NEWS: CompanyNewsFeed = {
  article_count: 1402,
  companies: [
    { symbol: 'JPM', name: 'JPMorgan Chase & Co.', company_news: { articles: [article(1, '2026-11-04T06:00:00Z', 'JPM tender offer', 'bloomberg.com', 'low')] } },
    { symbol: 'NVDA', name: 'NVIDIA Corporation', company_news: { articles: [
      article(2, '2026-11-04T06:20:00Z', 'Quantum stock rally', 'finance.yahoo.com'),
      article(3, '2026-11-03T12:00:00Z', 'NVIDIA guidance', 'reuters.com', 'high'),
    ] } },
  ],
}

const DECISIONS: CompanyDecisionsFeed = {
  last_updated_at: '2026-11-03T20:38:25Z',
  companies: [
    { symbol: 'PBF', name: 'PBF Energy Inc.', last_updated_at: '2026-11-03T20:30:00Z', strategist: { decision: 'trade_candidate' }, manager: { decision: 'neither' } },
    { symbol: 'ORCL', name: 'Oracle Corporation', last_updated_at: '2026-11-03T20:38:00Z', strategist: { decision: 'trade_candidate' }, manager: { decision: 'call' } },
    { symbol: 'XOM', name: 'Exxon Mobil', last_updated_at: '2026-11-03T20:10:00Z', strategist: { decision: 'watchlist' }, manager: { decision: 'neither' } },
  ],
}

describe('newsRows', () => {
  it('flattens every company into one feed, newest first', () => {
    expect(newsRows(NEWS).map((row) => [row.ticker, row.headline])).toEqual([
      ['NVDA', 'Quantum stock rally'],
      ['JPM', 'JPM tender offer'],
      ['NVDA', 'NVIDIA guidance'],
    ])
  })

  it('carries the assessed confidence, blank when the article was not assessed', () => {
    expect(newsRows(NEWS).map((row) => row.confidence)).toEqual(['', 'low', 'high'])
  })
})

describe('decisionRows and decisionSummary', () => {
  it('lists the latest run newest first', () => {
    expect(decisionRows(DECISIONS).map((row) => row.ticker)).toEqual(['ORCL', 'PBF', 'XOM'])
  })

  it('counts the run by manager verdict, leaving out verdicts nobody got', () => {
    expect(decisionSummary(DECISIONS)).toBe('3 in the latest run · 1 call · 2 no trade')
  })
})

describe('visibleRows', () => {
  const rows = Array.from({ length: 12 }, (_, index) => ({ id: String(index), text: index === 11 ? 'needle' : 'hay' }))

  it('shows the newest 8 when there is no search', () => {
    expect(visibleRows(rows, '', (row) => [row.text])).toHaveLength(8)
  })

  it('searches every loaded row, ignoring case', () => {
    expect(visibleRows(rows, 'NEED', (row) => [row.text]).map((row) => row.id)).toEqual(['11'])
  })
})

describe('Newsfeed panel', () => {
  it('filters by ticker, company, headline or source as you type', async () => {
    render(<LiveNewsfeedView feed={NEWS} />)

    const panel = screen.getByRole('region', { name: /Newsfeed/ })
    await userEvent.type(within(panel).getByRole('searchbox', { name: 'Search news' }), 'oracle')
    expect(within(panel).queryByText('Quantum stock rally')).toBeNull()

    await userEvent.clear(within(panel).getByRole('searchbox', { name: 'Search news' }))
    await userEvent.type(within(panel).getByRole('searchbox', { name: 'Search news' }), 'nvidia')
    expect(within(panel).getByText('Quantum stock rally')).toBeTruthy()
    expect(within(panel).getByText('NVIDIA guidance')).toBeTruthy()
    expect(within(panel).queryByText('JPM tender offer')).toBeNull()
  })

  it('offers the full news page when nothing loaded matches', async () => {
    render(<LiveNewsfeedView feed={NEWS} />)

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search news' }), 'tesla')
    expect(screen.getByText(/No articles match "tesla" in the loaded window/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'search all news' }).getAttribute('href')).toBe('#/decisions/news?q=tesla')
  })

  it('links each headline to its source', () => {
    render(<LiveNewsfeedView feed={NEWS} />)
    expect(screen.getByRole('link', { name: 'NVIDIA guidance' }).getAttribute('href')).toBe('https://reuters.com/3')
  })
})

describe('Decisions panel', () => {
  it('summarizes the run and shows each verdict pair', () => {
    render(<LiveDecisionsView feed={DECISIONS} />)

    const panel = screen.getByRole('region', { name: /Decisions/ })
    expect(panel.textContent).toContain('3 in the latest run · 1 call · 2 no trade')
    const orcl = within(panel).getByText('ORCL').closest('tr') as HTMLElement
    expect(orcl.textContent).toContain('Trade candidate')
    expect(orcl.textContent).toContain('Call')
  })

  it('offers the decision history when nothing in the run matches', async () => {
    render(<LiveDecisionsView feed={DECISIONS} />)

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search decisions' }), 'amd')
    expect(screen.getByText(/No decisions for "amd" in the latest run/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'search all history' }).getAttribute('href')).toBe('#/decisions/ledger?q=amd')
  })
})
