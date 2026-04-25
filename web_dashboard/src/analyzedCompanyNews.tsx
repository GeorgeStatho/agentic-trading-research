import { useEffect, useState } from 'react';
import './analyzedCompanyNews.css';

type ConfidenceLevel = 'all' | 'high' | 'medium' | 'low';
type ScopeView = 'company' | 'sector' | 'industry' | 'macro';

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

type NewsEntityCard = {
  id: string;
  label: string;
  title: string;
  subtitle: string;
  latest_published_at: string;
  confidence_counts: {
    high: number;
    medium: number;
    low: number;
  };
  articles: NewsArticle[];
};

const COMPANY_NEWS_POLL_INTERVAL_MS = 60_000;
const DEFAULT_PAGE_SIZE = 5;
const PAGE_SIZE_OPTIONS = [3, 5, 10, 15];
const VIEW_OPTIONS: Array<{ key: ScopeView; label: string; title: string; description: string }> = [
  {
    key: 'company',
    label: 'Company',
    title: 'Company Analysis',
    description: 'Company-specific opportunist analysis only.',
  },
  {
    key: 'sector',
    label: 'Sector',
    title: 'Sector News',
    description: 'Sector-level articles grouped by unique sector on the loaded page.',
  },
  {
    key: 'industry',
    label: 'Industry',
    title: 'Industry News',
    description: 'Industry-level articles grouped by unique industry on the loaded page.',
  },
  {
    key: 'macro',
    label: 'Macro',
    title: 'Macro News',
    description: 'Macro articles grouped by the sector they were mapped to.',
  },
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

function comparePublishedAtDescending(left: NewsArticle, right: NewsArticle): number {
  const leftTime = Date.parse(left.published_at);
  const rightTime = Date.parse(right.published_at);

  if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
    return rightTime - leftTime;
  }

  return right.article_id - left.article_id;
}

function dedupeArticles(articles: NewsArticle[]): NewsArticle[] {
  const articleMap = new Map<
    number,
    NewsArticle & {
      _assessmentKeys: Set<string>;
    }
  >();

  for (const article of articles) {
    const existingArticle = articleMap.get(article.article_id);
    const normalizedArticle =
      existingArticle ??
      ({
        ...article,
        assessments: [],
        _assessmentKeys: new Set<string>(),
      } satisfies NewsArticle & { _assessmentKeys: Set<string> });

    for (const assessment of article.assessments) {
      const assessmentKey = [
        assessment.confidence,
        assessment.impact_direction,
        assessment.impact_magnitude,
        assessment.reason,
        assessment.created_at,
        assessment.news_scope,
      ].join('|');

      if (normalizedArticle._assessmentKeys.has(assessmentKey)) {
        continue;
      }

      normalizedArticle._assessmentKeys.add(assessmentKey);
      normalizedArticle.assessments.push({ ...assessment });
    }

    articleMap.set(article.article_id, normalizedArticle);
  }

  return Array.from(articleMap.values())
    .map(({ _assessmentKeys: _ignored, ...article }) => article)
    .sort(comparePublishedAtDescending);
}

function summarizeConfidenceCounts(articles: NewsArticle[]): { high: number; medium: number; low: number } {
  const counts = { high: 0, medium: 0, low: 0 };

  for (const article of articles) {
    for (const assessment of article.assessments) {
      if (assessment.confidence === 'high' || assessment.confidence === 'medium' || assessment.confidence === 'low') {
        counts[assessment.confidence] += 1;
      }
    }
  }

  return counts;
}

function getLatestPublishedAt(articles: NewsArticle[]): string {
  return articles[0]?.published_at ?? '';
}

function buildCompanyCards(companies: CompanyNewsEntry[]): NewsEntityCard[] {
  return companies.map((company) => {
    const articles = dedupeArticles(company.company_news.articles);

    return {
      id: `company-${company.company_id}`,
      label: company.symbol,
      title: company.name,
      subtitle: `${formatLabel(company.sector_name || company.sector_key, 'Unknown sector')} / ${formatLabel(company.industry_name || company.industry_key, 'Unknown industry')}`,
      latest_published_at: getLatestPublishedAt(articles),
      confidence_counts: summarizeConfidenceCounts(articles),
      articles,
    };
  });
}

function buildSectorCards(companies: CompanyNewsEntry[]): NewsEntityCard[] {
  const sectorMap = new Map<
    string,
    {
      label: string;
      title: string;
      subtitle: string;
      companies: Set<string>;
      articles: NewsArticle[];
    }
  >();

  for (const company of companies) {
    const sectorId = company.sector_id ?? company.sector_key;
    const sectorKey = String(sectorId || company.symbol);
    const existingSector = sectorMap.get(sectorKey) ?? {
      label: formatLabel(company.sector_key, 'Sector'),
      title: formatLabel(company.sector_name || company.sector_key, 'Unknown sector'),
      subtitle: '',
      companies: new Set<string>(),
      articles: [],
    };

    existingSector.companies.add(company.symbol);
    existingSector.articles.push(...company.sector_news.articles);
    existingSector.subtitle = `${formatCount(existingSector.companies.size)} loaded companies in this sector`;
    sectorMap.set(sectorKey, existingSector);
  }

  return Array.from(sectorMap.entries()).map(([sectorKey, sector]) => {
    const articles = dedupeArticles(sector.articles);

    return {
      id: `sector-${sectorKey}`,
      label: sector.label,
      title: sector.title,
      subtitle: sector.subtitle,
      latest_published_at: getLatestPublishedAt(articles),
      confidence_counts: summarizeConfidenceCounts(articles),
      articles,
    };
  });
}

function buildIndustryCards(companies: CompanyNewsEntry[]): NewsEntityCard[] {
  const industryMap = new Map<
    string,
    {
      label: string;
      title: string;
      subtitle: string;
      companies: Set<string>;
      articles: NewsArticle[];
    }
  >();

  for (const company of companies) {
    const industryId = company.industry_id ?? company.industry_key;
    const industryKey = String(industryId || company.symbol);
    const existingIndustry = industryMap.get(industryKey) ?? {
      label: formatLabel(company.industry_key, 'Industry'),
      title: formatLabel(company.industry_name || company.industry_key, 'Unknown industry'),
      subtitle: '',
      companies: new Set<string>(),
      articles: [],
    };

    existingIndustry.companies.add(company.symbol);
    existingIndustry.articles.push(...company.industry_news.articles);
    existingIndustry.subtitle = `${formatCount(existingIndustry.companies.size)} loaded companies in this industry`;
    industryMap.set(industryKey, existingIndustry);
  }

  return Array.from(industryMap.entries()).map(([industryKey, industry]) => {
    const articles = dedupeArticles(industry.articles);

    return {
      id: `industry-${industryKey}`,
      label: industry.label,
      title: industry.title,
      subtitle: industry.subtitle,
      latest_published_at: getLatestPublishedAt(articles),
      confidence_counts: summarizeConfidenceCounts(articles),
      articles,
    };
  });
}

function buildMacroCards(companies: CompanyNewsEntry[]): NewsEntityCard[] {
  const macroMap = new Map<
    string,
    {
      label: string;
      title: string;
      subtitle: string;
      articles: NewsArticle[];
    }
  >();

  for (const company of companies) {
    const sectorId = company.sector_id ?? company.sector_key;
    const macroKey = String(sectorId || company.symbol);
    const existingMacro = macroMap.get(macroKey) ?? {
      label: formatLabel(company.sector_key, 'Macro'),
      title: formatLabel(company.sector_name || company.sector_key, 'Unknown sector'),
      subtitle: 'Macro signals mapped to this sector',
      articles: [],
    };

    existingMacro.articles.push(...company.macro_news.articles);
    macroMap.set(macroKey, existingMacro);
  }

  return Array.from(macroMap.entries()).map(([macroKey, macro]) => {
    const articles = dedupeArticles(macro.articles);

    return {
      id: `macro-${macroKey}`,
      label: macro.label,
      title: macro.title,
      subtitle: macro.subtitle,
      latest_published_at: getLatestPublishedAt(articles),
      confidence_counts: summarizeConfidenceCounts(articles),
      articles,
    };
  });
}

function buildCardsForView(companies: CompanyNewsEntry[], view: ScopeView): NewsEntityCard[] {
  const cards =
    view === 'company'
      ? buildCompanyCards(companies)
      : view === 'sector'
        ? buildSectorCards(companies)
        : view === 'industry'
          ? buildIndustryCards(companies)
          : buildMacroCards(companies);

  return cards.sort((left, right) => {
    const leftTime = Date.parse(left.latest_published_at);
    const rightTime = Date.parse(right.latest_published_at);

    if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
      return rightTime - leftTime;
    }

    return left.title.localeCompare(right.title);
  });
}

function matchesSearch(card: NewsEntityCard, searchValue: string): boolean {
  const needle = searchValue.trim().toLowerCase();
  if (!needle) {
    return true;
  }

  const articleText = card.articles
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
    card.label.toLowerCase().includes(needle) ||
    card.title.toLowerCase().includes(needle) ||
    card.subtitle.toLowerCase().includes(needle) ||
    articleText.includes(needle)
  );
}

function matchesConfidence(card: NewsEntityCard, confidence: ConfidenceLevel): boolean {
  if (confidence === 'all') {
    return true;
  }

  return card.articles.some((article) => article.assessments.some((assessment) => assessment.confidence === confidence));
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
  const [activeView, setActiveView] = useState<ScopeView>('company');
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

  const activeViewMeta = VIEW_OPTIONS.find((view) => view.key === activeView) ?? VIEW_OPTIONS[0];
  const viewCards = buildCardsForView(payload?.companies ?? [], activeView);
  const filteredCards = viewCards
    .filter((card) => matchesSearch(card, searchValue))
    .filter((card) => matchesConfidence(card, activeConfidence));

  return (
    <main className="company-news-page">
      <section className="company-news-hero">
        <div>
          <p className="company-news-hero__eyebrow">Opportunist Coverage</p>
          <h1>Analyzed News Context</h1>
          <p className="company-news-hero__text">
            Review company analysis separately from sector, industry, and macro coverage. The selector below changes
            which news layer is displayed for the currently loaded page.
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
            <p className="company-news-stat__label">Current View</p>
            <p className="company-news-stat__value company-news-stat__value--timestamp">{activeViewMeta.title}</p>
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

      <section className="company-news-view-selector" aria-label="News view selector">
        {VIEW_OPTIONS.map((view) => (
          <button
            key={view.key}
            type="button"
            className={`company-news-view-selector__button${activeView === view.key ? ' company-news-view-selector__button--active' : ''}`}
            onClick={() => setActiveView(view.key)}
          >
            <span className="company-news-view-selector__label">{view.label}</span>
            <span className="company-news-view-selector__description">{view.description}</span>
          </button>
        ))}
      </section>

      <section className="company-news-controls">
        <label className="company-news-search">
          <span>Search loaded {activeViewMeta.label.toLowerCase()} view</span>
          <input
            type="search"
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder={`Filter the current ${activeViewMeta.label.toLowerCase()} view by name, source, or reason`}
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
              {formatCount(payload.company_count)} companies. Sector, industry, and macro views are aggregated from the
              companies loaded on this page.
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
      ) : filteredCards.length === 0 ? (
        <section className="company-news-feedback">
          <h2>No matching {activeViewMeta.label.toLowerCase()} entries on this page</h2>
          <p>Try clearing the search, switching the confidence filter, or moving to another page.</p>
        </section>
      ) : (
        <section className="company-news-list">
          {filteredCards.map((card) => {
            const visibleArticles = filterArticles(card.articles, activeConfidence);

            return (
              <article key={card.id} className="company-news-company">
                <header className="company-news-company__header">
                  <div>
                    <p className="company-news-company__symbol">{card.label}</p>
                    <h2>{card.title}</h2>
                    <p className="company-news-company__meta">{card.subtitle}</p>
                  </div>
                  <div className="company-news-company__summary">
                    <span>{formatCount(visibleArticles.length)} visible articles</span>
                    <span>{formatCount(card.confidence_counts.high)} high confidence assessments</span>
                    <span>Latest context {card.latest_published_at ? formatDateTime(card.latest_published_at) : 'unknown'}</span>
                  </div>
                </header>

                <div className="company-news-article-list">
                  {visibleArticles.map((article) => {
                    const visibleAssessments =
                      activeConfidence === 'all'
                        ? article.assessments
                        : article.assessments.filter((assessment) => assessment.confidence === activeConfidence);

                    return (
                      <article key={`${card.id}-${article.article_id}`} className="company-news-article">
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
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}

export default AnalyzedCompanyNewsPage;
