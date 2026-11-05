import { describe, expect, it } from 'vitest'

import { buildCardsForView, dedupeArticles, feedRows } from './companyNews.ts'
import { newsArticle, newsCompany } from './test/newsFixture.ts'

describe('dedupeArticles', () => {
  it('merges repeats of one article, keeping each assessment once and the most finished status', () => {
    const assessment = newsArticle().assessments[0]
    const merged = dedupeArticles([
      newsArticle({ analysis_status: 'pending', assessments: [] }),
      newsArticle({ assessments: [assessment] }),
      newsArticle({ assessments: [assessment] }),
    ])

    expect(merged).toHaveLength(1)
    expect(merged[0].analysis_status).toBe('analyzed')
    expect(merged[0].assessments).toEqual([assessment])
  })

  it('orders articles newest first', () => {
    const merged = dedupeArticles([
      newsArticle({ article_id: 1, published_at: '2026-11-03T10:00:00Z' }),
      newsArticle({ article_id: 2, published_at: '2026-11-04T10:00:00Z' }),
    ])
    expect(merged.map((article) => article.article_id)).toEqual([2, 1])
  })
})

describe('buildCardsForView', () => {
  it('groups the loaded companies by sector for the sector view', () => {
    const companies = [
      newsCompany({ symbol: 'PLTR', sector_news: [newsArticle({ article_id: 7 })] }),
      newsCompany({ company_id: 2, symbol: 'MSFT', name: 'Microsoft', sector_news: [newsArticle({ article_id: 7 }), newsArticle({ article_id: 8 })] }),
    ]
    const cards = buildCardsForView(companies, 'sector')

    expect(cards).toHaveLength(1)
    expect(cards[0].title).toBe('Technology')
    expect(cards[0].subtitle).toBe('2 loaded companies in this sector')
    expect(cards[0].articles.map((article) => article.article_id).sort()).toEqual([7, 8])
  })
})

describe('feedRows', () => {
  const cards = buildCardsForView([
    newsCompany({ company_news: [
      newsArticle({ article_id: 1, published_at: '2026-11-04T10:00:00Z', title: 'Palantir wins Army deal' }),
      newsArticle({ article_id: 2, published_at: '2026-11-04T17:06:23Z', assessments: [{ ...newsArticle().assessments[0], confidence: 'high' }] }),
    ] }),
    newsCompany({ company_id: 2, symbol: 'MSFT', name: 'Microsoft Corporation', company_news: [
      newsArticle({ article_id: 3, published_at: '2026-11-04T12:00:00Z', title: 'Azure grows 48%', source: 'reuters.com', analysis_status: 'processed_no_assessment', assessments: [] }),
    ] }),
  ], 'company')

  function ids(confidence: Parameters<typeof feedRows>[1], query = '') {
    return feedRows(cards, confidence, query).map((row) => row.article.article_id)
  }

  it('flattens every group into one feed, newest first, each row naming its group', () => {
    const rows = feedRows(cards, 'all', '')
    expect(rows.map((row) => row.article.article_id)).toEqual([2, 3, 1])
    expect(rows.map((row) => row.label)).toEqual(['PLTR', 'MSFT', 'PLTR'])
  })

  it('keeps only articles with an assessment at the chosen confidence', () => {
    expect(ids('high')).toEqual([2])
    expect(ids('low')).toEqual([])
  })

  it('searches ticker, company, headline, source and reason', () => {
    expect(ids('all', 'msft')).toEqual([3])
    expect(ids('all', 'microsoft')).toEqual([3])
    expect(ids('all', 'army deal')).toEqual([1])
    expect(ids('all', 'REUTERS')).toEqual([3])
    expect(ids('all', 'defense spending')).toEqual([2, 1])
  })
})
