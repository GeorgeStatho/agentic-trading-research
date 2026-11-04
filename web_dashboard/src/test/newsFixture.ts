import type { CompanyNewsEntry, CompanyNewsPayload, NewsArticle } from '../companyNews.ts'

/** One `/api/opportunist-company-news` article, analyzed, shaped like the live ones. */
export function newsArticle(overrides: Partial<NewsArticle> = {}): NewsArticle {
  return {
    article_id: 16435,
    title: 'Why Palantir Technologies Stock Dropped Today',
    summary: '',
    body_preview: 'The 2026 midterm results are in.',
    source: 'finance.yahoo.com',
    source_url: 'https://finance.yahoo.com/markets/stocks/articles/why-palantir.html',
    published_at: '2026-11-04T17:06:23Z',
    analysis_status: 'analyzed',
    assessments: [{
      confidence: 'medium',
      created_at: '2026-11-04 17:20:28',
      impact_direction: 'negative',
      impact_magnitude: 'moderate',
      news_scope: '',
      reason: 'Uncertainty about future defense spending, a key revenue driver.',
    }],
    ...overrides,
  }
}

function section(articles: NewsArticle[]) {
  return { article_count: articles.length, latest_published_at: articles[0]?.published_at ?? '', confidence_counts: { high: 0, medium: 0, low: 0 }, articles }
}

type CompanyOverrides = Partial<Omit<CompanyNewsEntry, 'company_news' | 'sector_news' | 'industry_news' | 'macro_news'>> & {
  company_news?: NewsArticle[]
  sector_news?: NewsArticle[]
  industry_news?: NewsArticle[]
  macro_news?: NewsArticle[]
}

export function newsCompany(overrides: CompanyOverrides = {}): CompanyNewsEntry {
  const { company_news = [newsArticle()], sector_news = [], industry_news = [], macro_news = [], ...rest } = overrides
  return {
    company_id: 2371,
    symbol: 'PLTR',
    name: 'Palantir Technologies Inc.',
    industry_id: 129,
    industry_key: 'software-infrastructure',
    industry_name: 'Software - Infrastructure',
    sector_id: 10,
    sector_key: 'technology',
    sector_name: 'Technology',
    analyzed_article_count: company_news.length,
    latest_published_at: company_news[0]?.published_at ?? '',
    confidence_counts: { high: 0, medium: 0, low: 0 },
    company_news: section(company_news),
    sector_news: section(sector_news),
    industry_news: section(industry_news),
    macro_news: section(macro_news),
    ...rest,
  }
}

export function newsPayload(overrides: Partial<CompanyNewsPayload> = {}): CompanyNewsPayload {
  return {
    as_of: '2026-11-04T17:27:54+00:00',
    active_view: 'company',
    lookup_query: '',
    lookup_active: false,
    lookup_match_count: 105,
    page: 1,
    page_size: 5,
    total_pages: 21,
    has_previous_page: false,
    has_next_page: true,
    page_start: 1,
    page_end: 5,
    page_company_count: 1,
    company_count: 105,
    article_count: 1909,
    section_article_counts: { company: 1909, sector: 0, industry: 0, macro: 0 },
    companies: [newsCompany()],
    ...overrides,
  }
}
