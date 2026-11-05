import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AnalyzedCompanyNewsPage from './analyzedCompanyNews.tsx'
import type { CompanyNewsPayload } from './companyNews.ts'
import { newsArticle, newsCompany, newsPayload } from './test/newsFixture.ts'

let answer: CompanyNewsPayload
const requests: URLSearchParams[] = []

beforeEach(() => {
  answer = newsPayload()
  requests.length = 0
  window.location.hash = '#/decisions/news'
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(new URL(String(input), 'http://localhost').searchParams)
    return Response.json(answer)
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function lastRequest(): URLSearchParams {
  return requests[requests.length - 1]
}

function feed() {
  return screen.getByRole('list', { name: 'Articles' })
}

describe('Company news page', () => {
  it('renders each article as one dense row: time, ticker, headline link, source, confidence and reason', async () => {
    render(<AnalyzedCompanyNewsPage />)

    const row = within(await screen.findByRole('list', { name: 'Articles' })).getAllByRole('listitem')[0]
    expect(within(row).getByText('PLTR')).toBeTruthy()
    expect(within(row).getByRole('link', { name: 'Why Palantir Technologies Stock Dropped Today' }).getAttribute('href')).toContain('finance.yahoo.com')
    expect(row.textContent).toContain('finance.yahoo.com')
    expect(row.textContent).toContain('Medium')
    expect(row.textContent).toContain('negative · moderate')
    expect(row.textContent).toContain('Uncertainty about future defense spending')
  })

  it('says when the opportunist found nothing material', async () => {
    answer = newsPayload({ companies: [newsCompany({ company_news: [newsArticle({ analysis_status: 'processed_no_assessment', assessments: [] })] })] })
    render(<AnalyzedCompanyNewsPage />)

    expect((await screen.findByRole('list', { name: 'Articles' })).textContent).toContain('No material impact')
  })

  it('puts search, scope, confidence and page size in one toolbar', async () => {
    render(<AnalyzedCompanyNewsPage />)
    await screen.findByRole('list', { name: 'Articles' })

    const toolbar = screen.getByRole('search')
    expect(within(toolbar).getByRole('searchbox', { name: 'Search news' })).toBeTruthy()
    expect(within(toolbar).getByRole('group', { name: 'Scope' })).toBeTruthy()
    expect(within(toolbar).getByRole('combobox', { name: 'Confidence' })).toBeTruthy()
    expect(within(toolbar).getByRole('combobox', { name: 'Companies per page' })).toBeTruthy()
  })

  it('shows the article count on every scope', async () => {
    render(<AnalyzedCompanyNewsPage />)
    await screen.findByRole('list', { name: 'Articles' })

    const scope = screen.getByRole('group', { name: 'Scope' })
    expect(within(scope).getByRole('button', { name: /Company/ }).textContent).toContain('1,909')
    expect(within(scope).getByRole('button', { name: /Sector/ }).textContent).toContain('412')
    expect(within(scope).getByRole('button', { name: /Macro/ }).textContent).toContain('98')
  })

  it('bars each article by its evidence score, when it has one', async () => {
    answer = newsPayload({ companies: [newsCompany({ company_news: [newsArticle(), newsArticle({ article_id: 2, evidence_score: null })] })] })
    render(<AnalyzedCompanyNewsPage />)

    const rows = within(await screen.findByRole('list', { name: 'Articles' })).getAllByRole('listitem')
    expect(within(rows[0]).getByRole('img', { name: 'Evidence score 0.81' })).toBeTruthy()
    expect(within(rows[1]).queryByRole('img', { name: /Evidence score/ })).toBeNull()
  })

  it('asks the API for another scope', async () => {
    render(<AnalyzedCompanyNewsPage />)
    await screen.findByRole('list', { name: 'Articles' })

    await userEvent.click(within(screen.getByRole('group', { name: 'Scope' })).getByRole('button', { name: /Sector/ }))
    await waitFor(() => expect(lastRequest().get('view')).toBe('sector'))
  })

  it('filters the loaded articles as you type, and looks the company up on Enter', async () => {
    answer = newsPayload({ companies: [
      newsCompany(),
      newsCompany({ company_id: 2, symbol: 'MSFT', name: 'Microsoft', company_news: [newsArticle({ article_id: 3, title: 'Azure grows 48%' })] }),
    ] })
    render(<AnalyzedCompanyNewsPage />)
    await screen.findByRole('list', { name: 'Articles' })

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search news' }), 'azure')
    expect(within(feed()).getAllByRole('listitem')).toHaveLength(1)
    expect(lastRequest().get('company')).toBeNull()

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search news' }), '{Enter}')
    await waitFor(() => expect(lastRequest().get('company')).toBe('azure'))
  })

  it('looks up the search a link carries', async () => {
    window.location.hash = '#/decisions/news?q=PLTR'
    render(<AnalyzedCompanyNewsPage />)

    await waitFor(() => expect(lastRequest().get('company')).toBe('PLTR'))
    expect((screen.getByRole('searchbox', { name: 'Search news' }) as HTMLInputElement).value).toBe('PLTR')
  })

  it('filters by confidence', async () => {
    render(<AnalyzedCompanyNewsPage />)
    await screen.findByRole('list', { name: 'Articles' })

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Confidence' }), 'high')
    expect(screen.queryByRole('list', { name: 'Articles' })).toBeNull()
    expect(screen.getByText(/No articles match/)).toBeTruthy()
  })

  it('pages through companies and changes the page size', async () => {
    render(<AnalyzedCompanyNewsPage />)
    await screen.findByRole('list', { name: 'Articles' })

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(lastRequest().get('page')).toBe('2'))

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Companies per page' }), '10')
    await waitFor(() => expect(lastRequest().get('page_size')).toBe('10'))
    expect(lastRequest().get('page')).toBe('1')
  })

  it('shows 50 rows at a time', async () => {
    const articles = Array.from({ length: 60 }, (_, index) => newsArticle({ article_id: index + 1, published_at: `2026-11-04T${String(index % 24).padStart(2, '0')}:00:00Z` }))
    answer = newsPayload({ companies: [newsCompany({ company_news: articles })] })
    render(<AnalyzedCompanyNewsPage />)

    expect(within(await screen.findByRole('list', { name: 'Articles' })).getAllByRole('listitem')).toHaveLength(50)
    await userEvent.click(screen.getByRole('button', { name: 'Show 10 more' }))
    expect(within(feed()).getAllByRole('listitem')).toHaveLength(60)
  })
})
