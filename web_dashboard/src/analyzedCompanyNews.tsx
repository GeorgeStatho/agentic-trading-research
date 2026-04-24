import { useEffect, useState } from 'react';
import './analyzedCompanyNews.css';

type ArticleAssessment = {
  confidence: string;
  impact_direction: string;
  impact_magnitude: string;
  reason: string;
  created_at: string;
};

type CompanyArticle = {
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

type CompanyNewsEntry = {
  company_id: number;
  symbol: string;
  name: string;
  industry_key: string;
  sector_key: string;
  analyzed_article_count: number;
  latest_published_at: string;
  confidence_counts: {
    high: number;
    medium: number;
    low: number;
  };
  articles: CompanyArticle[];
};

type CompanyNewsPayload = {
  as_of: string;
  company_count: number;
  article_count: number;
  companies: CompanyNewsEntry[];
};

const COMPANY_NEWS_POLL_INTERVAL_MS = 60_000;

async function getAnalyzedCompanyNews(): Promise<CompanyNewsPayload> {
  const response = await fetch(`/api/opportunist-company-news?ts=${Date.now()}`);

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

function matchesSearch(company: CompanyNewsEntry, searchValue: string): boolean {
  const needle = searchValue.trim().toLowerCase();
  if (!needle) {
    return true;
  }

  const articleText = company.articles
    .flatMap((article) => [
      article.title,
      article.summary,
      article.source,
      ...article.assessments.map((assessment) => assessment.reason),
    ])
    .join(' ')
    .toLowerCase();

  return (
    company.symbol.toLowerCase().includes(needle) ||
    company.name.toLowerCase().includes(needle) ||
    company.industry_key.toLowerCase().includes(needle) ||
    company.sector_key.toLowerCase().includes(needle) ||
    articleText.includes(needle)
  );
}

function matchesConfidence(company: CompanyNewsEntry, confidence: string): boolean {
  if (confidence === 'all') {
    return true;
  }

  return company.articles.some((article) =>
    article.assessments.some((assessment) => assessment.confidence === confidence),
  );
}

function filterArticles(articles: CompanyArticle[], confidence: string): CompanyArticle[] {
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
  const [searchValue, setSearchValue] = useState('');
  const [activeConfidence, setActiveConfidence] = useState<'all' | 'high' | 'medium' | 'low'>('all');

  useEffect(() => {
    let isMounted = true;

    const loadCompanyNews = () => {
      getAnalyzedCompanyNews()
        .then((nextPayload) => {
          if (!isMounted) {
            return;
          }

          setPayload(nextPayload);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!isMounted) {
            return;
          }

          setError(err instanceof Error ? err.message : 'Failed to load analyzed company news.');
        });
    };

    loadCompanyNews();
    const intervalId = window.setInterval(loadCompanyNews, COMPANY_NEWS_POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

  const filteredCompanies = (payload?.companies ?? [])
    .filter((company) => matchesSearch(company, searchValue))
    .filter((company) => matchesConfidence(company, activeConfidence));

  return (
    <main className="company-news-page">
      <section className="company-news-hero">
        <div>
          <p className="company-news-hero__eyebrow">Opportunist Coverage</p>
          <h1>Analyzed Company News</h1>
          <p className="company-news-hero__text">
            Review the company-level articles the opportunist pipeline analyzed for the current ranked companies.
          </p>
        </div>
        <div className="company-news-hero__stats" aria-label="Company news summary">
          <article className="company-news-stat">
            <p className="company-news-stat__label">Companies</p>
            <p className="company-news-stat__value">{formatCount(payload?.company_count ?? 0)}</p>
          </article>
          <article className="company-news-stat">
            <p className="company-news-stat__label">Articles</p>
            <p className="company-news-stat__value">{formatCount(payload?.article_count ?? 0)}</p>
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
          <span>Search companies or headlines</span>
          <input
            type="search"
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder="Search by ticker, company, source, or reason"
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

      {error ? (
        <section className="company-news-feedback">
          <h2>Company news unavailable</h2>
          <p>{error}</p>
        </section>
      ) : !payload ? (
        <section className="company-news-feedback">
          <h2>Loading analyzed company news</h2>
          <p>Reading the latest opportunist article coverage from the API.</p>
        </section>
      ) : filteredCompanies.length === 0 ? (
        <section className="company-news-feedback">
          <h2>No matching company articles</h2>
          <p>Try clearing the search or switching the confidence filter.</p>
        </section>
      ) : (
        <section className="company-news-list">
          {filteredCompanies.map((company) => {
            const visibleArticles = filterArticles(company.articles, activeConfidence);

            return (
              <article key={company.company_id} className="company-news-company">
                <header className="company-news-company__header">
                  <div>
                    <p className="company-news-company__symbol">{company.symbol}</p>
                    <h2>{company.name}</h2>
                    <p className="company-news-company__meta">
                      {company.sector_key || 'Unknown sector'} / {company.industry_key || 'Unknown industry'}
                    </p>
                  </div>
                  <div className="company-news-company__summary">
                    <span>{formatCount(visibleArticles.length)} visible articles</span>
                    <span>{company.confidence_counts.high} high confidence assessments</span>
                    <span>
                      Latest article {company.latest_published_at ? formatDateTime(company.latest_published_at) : 'unknown'}
                    </span>
                  </div>
                </header>

                <div className="company-news-article-list">
                  {visibleArticles.map((article) => (
                    (() => {
                      const visibleAssessments =
                        activeConfidence === 'all'
                          ? article.assessments
                          : article.assessments.filter((assessment) => assessment.confidence === activeConfidence)

                      return (
                        <article key={`${company.company_id}-${article.article_id}`} className="company-news-article">
                          <div className="company-news-article__topline">
                            <p className="company-news-article__source">
                              {article.source || 'Unknown source'} / {formatDateTime(article.published_at)}
                            </p>
                            <div className="company-news-article__chips">
                              {visibleAssessments.map((assessment, index) => (
                                <span
                                  key={`${article.article_id}-${assessment.created_at}-${index}`}
                                  className={`company-news-chip company-news-chip--${assessment.confidence || 'neutral'}`}
                                >
                                  {assessment.confidence || 'unknown'}
                                </span>
                              ))}
                            </div>
                          </div>

                          <h3>
                            {article.source_url ? (
                              <a href={article.source_url} target="_blank" rel="noreferrer">
                                {article.title || 'Untitled article'}
                              </a>
                            ) : (
                              article.title || 'Untitled article'
                            )}
                          </h3>

                          <p className="company-news-article__summary">
                            {article.summary || article.body_preview || 'No summary available.'}
                          </p>

                          <div className="company-news-assessment-list">
                            {visibleAssessments.map((assessment, index) => (
                              <article
                                key={`${article.article_id}-${assessment.reason}-${index}`}
                                className="company-news-assessment"
                              >
                                <div className="company-news-assessment__badges">
                                  <span className={`company-news-chip company-news-chip--${assessment.confidence || 'neutral'}`}>
                                    {assessment.confidence || 'unknown'}
                                  </span>
                                  <span className="company-news-chip company-news-chip--direction">
                                    {assessment.impact_direction || 'direction n/a'}
                                  </span>
                                  <span className="company-news-chip company-news-chip--magnitude">
                                    {assessment.impact_magnitude || 'magnitude n/a'}
                                  </span>
                                </div>
                                <p className="company-news-assessment__reason">
                                  {assessment.reason || 'No opportunist reason recorded.'}
                                </p>
                              </article>
                            ))}
                          </div>
                        </article>
                      );
                    })()
                  ))}
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
