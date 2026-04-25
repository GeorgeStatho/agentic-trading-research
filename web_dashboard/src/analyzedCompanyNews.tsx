import { useEffect, useState } from 'react';
import './analyzedCompanyNews.css';

type ConfidenceLevel = 'all' | 'high' | 'medium' | 'low';
type NewsSectionKey = 'company_news' | 'sector_news' | 'industry_news' | 'macro_news';

type ArticleAssessment = {
  confidence: string;
  impact_direction: string;
  impact_magnitude: string;
  reason: string;
  created_at: string;
  news_scope: string;
};

type NewsArticle = {
  article_id: number;
  title: string;
  summary: string;
  body_preview: string;
  source: string;
  source_url: string;
  published_at: string;
  processed_at: string;
  model: string;
  assessments: ArticleAssessment[];
};

type NewsSection = {
  article_count: number;
  latest_published_at: string;
  confidence_counts: {
    high: number;
    medium: number;
    low: number;
  };
  articles: NewsArticle[];
};

type CompanyNewsEntry = {
  company_id: number;
  symbol: string;
  name: string;
  industry_id: number | null;
  industry_key: string;
  industry_name: string;
  sector_id: number | null;
  sector_key: string;
  sector_name: string;
  analyzed_article_count: number;
  latest_published_at: string;
  confidence_counts: {
    high: number;
    medium: number;
    low: number;
  };
  company_news: NewsSection;
  sector_news: NewsSection;
  industry_news: NewsSection;
  macro_news: NewsSection;
};

type CompanyNewsPayload = {
  as_of: string;
  page: number;
  page_size: number;
  total_pages: number;
  has_previous_page: boolean;
  has_next_page: boolean;
  page_start: number;
  page_end: number;
  page_company_count: number;
  company_count: number;
  article_count: number;
  section_article_counts: {
    company: number;
    sector: number;
    industry: number;
    macro: number;
  };
  companies: CompanyNewsEntry[];
};

const COMPANY_NEWS_POLL_INTERVAL_MS = 60_000;
const DEFAULT_PAGE_SIZE = 5;
const PAGE_SIZE_OPTIONS = [3, 5, 10, 15];
const SECTION_ORDER: Array<{ key: NewsSectionKey; title: string; eyebrow: string }> = [
  { key: 'company_news', title: 'Company-Specific News', eyebrow: 'Company' },
  { key: 'sector_news', title: 'Sector News', eyebrow: 'Sector' },
  { key: 'industry_news', title: 'Industry News', eyebrow: 'Industry' },
  { key: 'macro_news', title: 'Macro News', eyebrow: 'Macro' },
];

async function getAnalyzedCompanyNews(page: number, pageSize: number): Promise<CompanyNewsPayload> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    ts: String(Date.now()),
  });
  const response = await fetch(`/api/opportunist-company-news?${params.toString()}`);

  if (!response.ok) {
    throw new Error(`Failed to load analyzed company news: ${response.status}`);
  }

  return response.json() as Promise<CompanyNewsPayload>;
}

function formatDateTime(value: string): string {
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return 'Unknown time';
  }

  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(timestamp));
}

function formatCount(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatLabel(value: string, fallback = 'Unknown'): string {
  const normalized = value.trim();
  return normalized || fallback;
}

function getAllSectionArticles(company: CompanyNewsEntry): NewsArticle[] {
  return SECTION_ORDER.flatMap(({ key }) => company[key].articles);
}

function getVisibleArticleCount(company: CompanyNewsEntry, confidence: ConfidenceLevel): number {
  return SECTION_ORDER.reduce((count, { key }) => count + filterArticles(company[key].articles, confidence).length, 0);
}

function getSectionContext(company: CompanyNewsEntry, key: NewsSectionKey): string {
  if (key === 'company_news') {
    return `${formatLabel(company.name, 'Company')} specific coverage`;
  }

  if (key === 'sector_news') {
    return formatLabel(company.sector_name || company.sector_key, 'Sector context');
  }

  if (key === 'industry_news') {
    return formatLabel(company.industry_name || company.industry_key, 'Industry context');
  }

  return `Macro signals mapped to ${formatLabel(company.sector_name || company.sector_key, 'this sector')}`;
}

function matchesSearch(company: CompanyNewsEntry, searchValue: string): boolean {
  const needle = searchValue.trim().toLowerCase();
  if (!needle) {
    return true;
  }

  const articleText = getAllSectionArticles(company)
    .flatMap((article) => [
      article.title,
      article.summary,
      article.body_preview,
      article.source,
      ...article.assessments.flatMap((assessment) => [assessment.reason, assessment.news_scope]),
    ])
    .join(' ')
    .toLowerCase();

  return (
    company.symbol.toLowerCase().includes(needle) ||
    company.name.toLowerCase().includes(needle) ||
    company.industry_key.toLowerCase().includes(needle) ||
    company.industry_name.toLowerCase().includes(needle) ||
    company.sector_key.toLowerCase().includes(needle) ||
    company.sector_name.toLowerCase().includes(needle) ||
    articleText.includes(needle)
  );
}

function matchesConfidence(company: CompanyNewsEntry, confidence: ConfidenceLevel): boolean {
  if (confidence === 'all') {
    return true;
  }

  return getAllSectionArticles(company).some((article) =>
    article.assessments.some((assessment) => assessment.confidence === confidence),
  );
}

function filterArticles(articles: NewsArticle[], confidence: ConfidenceLevel): NewsArticle[] {
  if (confidence === 'all') {
    return articles;
  }

  return articles.filter((article) =>
    article.assessments.some((assessment) => assessment.confidence === confidence),
  );
}

function AnalyzedCompanyNewsPage() {
  const [payload, setPayload] = useState<CompanyNewsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [searchValue, setSearchValue] = useState('');
  const [activeConfidence, setActiveConfidence] = useState<ConfidenceLevel>('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  useEffect(() => {
    let isMounted = true;

    const loadCompanyNews = () => {
      setIsLoading(true);
      getAnalyzedCompanyNews(currentPage, pageSize)
        .then((nextPayload) => {
          if (!isMounted) {
            return;
          }

          setPayload(nextPayload);
          setError(null);
          if (nextPayload.page !== currentPage) {
            setCurrentPage(nextPayload.page);
          }
        })
        .catch((err: unknown) => {
          if (!isMounted) {
            return;
          }

          setError(err instanceof Error ? err.message : 'Failed to load analyzed company news.');
        })
        .finally(() => {
          if (!isMounted) {
            return;
          }
          setIsLoading(false);
        });
    };

    loadCompanyNews();
    const intervalId = window.setInterval(loadCompanyNews, COMPANY_NEWS_POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, [currentPage, pageSize]);

  const filteredCompanies = (payload?.companies ?? [])
    .filter((company) => matchesSearch(company, searchValue))
    .filter((company) => matchesConfidence(company, activeConfidence));

  return (
    <main className="company-news-page">
      <section className="company-news-hero">
        <div>
          <p className="company-news-hero__eyebrow">Opportunist Coverage</p>
          <h1>Analyzed News Context</h1>
          <p className="company-news-hero__text">
            Review the company, sector, industry, and macro context the opportunist pipeline analyzed for the current
            ranked companies one page at a time.
          </p>
        </div>
        <div className="company-news-hero__stats" aria-label="Company news summary">
          <article className="company-news-stat">
            <p className="company-news-stat__label">Companies</p>
            <p className="company-news-stat__value">{formatCount(payload?.company_count ?? 0)}</p>
          </article>
          <article className="company-news-stat">
            <p className="company-news-stat__label">Loaded This Page</p>
            <p className="company-news-stat__value">{formatCount(payload?.page_company_count ?? 0)}</p>
          </article>
          <article className="company-news-stat">
            <p className="company-news-stat__label">Loaded Articles</p>
            <p className="company-news-stat__value">{formatCount(payload?.article_count ?? 0)}</p>
          </article>
          <article className="company-news-stat">
            <p className="company-news-stat__label">Page</p>
            <p className="company-news-stat__value company-news-stat__value--timestamp">
              {payload ? `${payload.page} / ${payload.total_pages || 1}` : 'Loading'}
            </p>
          </article>
          <article className="company-news-stat">
            <p className="company-news-stat__label">Loaded Split</p>
            <p className="company-news-stat__value company-news-stat__value--stacked">
              C {formatCount(payload?.section_article_counts.company ?? 0)} / S{' '}
              {formatCount(payload?.section_article_counts.sector ?? 0)} / I{' '}
              {formatCount(payload?.section_article_counts.industry ?? 0)} / M{' '}
              {formatCount(payload?.section_article_counts.macro ?? 0)}
            </p>
          </article>
          <article className="company-news-stat">
            <p className="company-news-stat__label">Updated</p>
            <p className="company-news-stat__value company-news-stat__value--timestamp">
              {payload ? formatDateTime(payload.as_of) : 'Loading'}
            </p>
          </article>
        </div>
      </section>

      <section className="company-news-controls">
        <label className="company-news-search">
          <span>Search loaded page</span>
          <input
            type="search"
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder="Filter the current page by ticker, company, sector, industry, source, or reason"
          />
        </label>
        <div className="company-news-filter-group" aria-label="Confidence filter">
          {(['all', 'high', 'medium', 'low'] as const).map((confidence) => (
            <button
              key={confidence}
              type="button"
              className={`company-news-filter${activeConfidence === confidence ? ' company-news-filter--active' : ''}`}
              onClick={() => setActiveConfidence(confidence)}
            >
              {confidence === 'all' ? 'All confidence' : `${confidence} confidence`}
            </button>
          ))}
        </div>
      </section>

      <section className="company-news-pagination" aria-label="Pagination controls">
        <div className="company-news-pagination__summary">
          {payload ? (
            <>
              Showing {formatCount(payload.page_start)}-{formatCount(payload.page_end)} of{' '}
              {formatCount(payload.company_count)} companies. Search filters only the loaded page.
            </>
          ) : (
            'Preparing the first page of analyzed news.'
          )}
        </div>
        <div className="company-news-pagination__controls">
          <label className="company-news-pagination__size">
            <span>Companies per page</span>
            <select
              value={pageSize}
              onChange={(event) => {
                const nextPageSize = Number(event.target.value);
                setPageSize(nextPageSize);
                setCurrentPage(1);
              }}
              disabled={isLoading}
            >
              {PAGE_SIZE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="company-news-pagination__button"
            onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
            disabled={isLoading || !payload?.has_previous_page}
          >
            Previous
          </button>
          <span className="company-news-pagination__page">
            {payload ? `Page ${payload.page} of ${payload.total_pages || 1}` : 'Loading page'}
          </span>
          <button
            type="button"
            className="company-news-pagination__button"
            onClick={() => setCurrentPage((page) => page + 1)}
            disabled={isLoading || !payload?.has_next_page}
          >
            Next
          </button>
        </div>
      </section>

      {error ? (
        <section className="company-news-feedback">
          <h2>Analyzed news unavailable</h2>
          <p>{error}</p>
        </section>
      ) : !payload ? (
        <section className="company-news-feedback">
          <h2>Loading analyzed news context</h2>
          <p>Reading the latest company, sector, industry, and macro coverage from the API.</p>
        </section>
      ) : filteredCompanies.length === 0 ? (
        <section className="company-news-feedback">
          <h2>No matching analyzed news on this page</h2>
          <p>Try clearing the search, switching the confidence filter, or moving to another page.</p>
        </section>
      ) : (
        <section className="company-news-list">
          {filteredCompanies.map((company) => {
            const visibleArticleCount = getVisibleArticleCount(company, activeConfidence);

            return (
              <article key={company.company_id} className="company-news-company">
                <header className="company-news-company__header">
                  <div>
                    <p className="company-news-company__symbol">{company.symbol}</p>
                    <h2>{company.name}</h2>
                    <p className="company-news-company__meta">
                      {formatLabel(company.sector_name || company.sector_key, 'Unknown sector')} /{' '}
                      {formatLabel(company.industry_name || company.industry_key, 'Unknown industry')}
                    </p>
                  </div>
                  <div className="company-news-company__summary">
                    <span>{formatCount(visibleArticleCount)} visible articles</span>
                    <span>{formatCount(company.confidence_counts.high)} high confidence assessments</span>
                    <span>
                      Latest context {company.latest_published_at ? formatDateTime(company.latest_published_at) : 'unknown'}
                    </span>
                  </div>
                </header>

                <div className="company-news-scope-list">
                  {SECTION_ORDER.map(({ key, title, eyebrow }) => {
                    const section = company[key];
                    const visibleArticles = filterArticles(section.articles, activeConfidence);

                    if (visibleArticles.length === 0) {
                      return null;
                    }

                    return (
                      <section key={`${company.company_id}-${key}`} className="company-news-scope">
                        <header className="company-news-scope__header">
                          <div>
                            <p className="company-news-scope__eyebrow">{eyebrow}</p>
                            <h3>{title}</h3>
                            <p className="company-news-scope__meta">{getSectionContext(company, key)}</p>
                          </div>
                          <div className="company-news-scope__summary">
                            <span>{formatCount(visibleArticles.length)} visible</span>
                            <span>{formatCount(section.confidence_counts.high)} high confidence</span>
                          </div>
                        </header>

                        <div className="company-news-article-list">
                          {visibleArticles.map((article) => {
                            const visibleAssessments =
                              activeConfidence === 'all'
                                ? article.assessments
                                : article.assessments.filter((assessment) => assessment.confidence === activeConfidence);

                            return (
                              <article key={`${company.company_id}-${key}-${article.article_id}`} className="company-news-article">
                                <div className="company-news-article__topline">
                                  <p className="company-news-article__source">
                                    {formatLabel(article.source, 'Unknown source')} / {formatDateTime(article.published_at)}
                                  </p>
                                  <div className="company-news-article__chips">
                                    {visibleAssessments.map((assessment, index) => (
                                      <span
                                        key={`${article.article_id}-${assessment.created_at}-${assessment.news_scope}-${index}`}
                                        className={`company-news-chip company-news-chip--${assessment.confidence || 'neutral'}`}
                                      >
                                        {assessment.confidence || 'unknown'}
                                      </span>
                                    ))}
                                  </div>
                                </div>

                                <h4>
                                  {article.source_url ? (
                                    <a href={article.source_url} target="_blank" rel="noreferrer">
                                      {article.title || 'Untitled article'}
                                    </a>
                                  ) : (
                                    article.title || 'Untitled article'
                                  )}
                                </h4>

                                <p className="company-news-article__summary">
                                  {article.summary || article.body_preview || 'No summary available.'}
                                </p>

                                <div className="company-news-assessment-list">
                                  {visibleAssessments.map((assessment, index) => {
                                    const hasDirectionalData = Boolean(
                                      assessment.impact_direction.trim() || assessment.impact_magnitude.trim(),
                                    );

                                    return (
                                      <article
                                        key={`${article.article_id}-${assessment.reason}-${assessment.news_scope}-${index}`}
                                        className="company-news-assessment"
                                      >
                                        <div className="company-news-assessment__badges">
                                          <span
                                            className={`company-news-chip company-news-chip--${assessment.confidence || 'neutral'}`}
                                          >
                                            {assessment.confidence || 'unknown'}
                                          </span>
                                          {assessment.news_scope ? (
                                            <span className="company-news-chip company-news-chip--neutral">
                                              {assessment.news_scope} macro
                                            </span>
                                          ) : null}
                                          {hasDirectionalData ? (
                                            <>
                                              <span className="company-news-chip company-news-chip--direction">
                                                {assessment.impact_direction || 'direction n/a'}
                                              </span>
                                              <span className="company-news-chip company-news-chip--magnitude">
                                                {assessment.impact_magnitude || 'magnitude n/a'}
                                              </span>
                                            </>
                                          ) : null}
                                        </div>
                                        <p className="company-news-assessment__reason">
                                          {assessment.reason || 'No opportunist reason recorded.'}
                                        </p>
                                      </article>
                                    );
                                  })}
                                </div>
                              </article>
                            );
                          })}
                        </div>
                      </section>
                    );
                  })}
                </div>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}

export default AnalyzedCompanyNewsPage;
