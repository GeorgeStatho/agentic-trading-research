import { fetchPolledJson } from './api';
import { asUtcTimestamp, formatNumber, formatText } from './format';

/** `/api/opportunist-company-news`: analyzed articles for one page of companies, by scope. */

export type ConfidenceLevel = 'all' | 'high' | 'medium' | 'low';
export type ScopeView = 'company' | 'sector' | 'industry' | 'macro';

export type ArticleAssessment = {
  confidence: string;
  impact_direction: string;
  impact_magnitude: string;
  reason: string;
  created_at: string;
  news_scope: string;
};

export type NewsArticle = {
  article_id: number;
  title: string;
  summary: string;
  body_preview: string;
  source: string;
  source_url: string;
  published_at: string;
  analysis_status: 'analyzed' | 'processed_no_assessment' | 'pending';
  assessments: ArticleAssessment[];
};

type ConfidenceCounts = { high: number; medium: number; low: number };

type NewsSection = {
  article_count: number;
  latest_published_at: string;
  confidence_counts: ConfidenceCounts;
  articles: NewsArticle[];
};

export type CompanyNewsEntry = {
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
  confidence_counts: ConfidenceCounts;
  company_news: NewsSection;
  sector_news: NewsSection;
  industry_news: NewsSection;
  macro_news: NewsSection;
};

export type CompanyNewsPayload = {
  as_of: string;
  active_view: ScopeView;
  lookup_query: string;
  lookup_active: boolean;
  lookup_match_count: number;
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
  /** Only the requested view's count is filled in; the others come back 0. */
  section_article_counts: Record<ScopeView, number>;
  companies: CompanyNewsEntry[];
};

/** One company, sector, industry or macro group and its articles. */
export type NewsCard = {
  id: string;
  label: string;
  title: string;
  subtitle: string;
  latest_published_at: string;
  articles: NewsArticle[];
};

export type NewsQuery = { page: number; pageSize: number; view: ScopeView; company: string };

export function getCompanyNews({ page, pageSize, view, company }: NewsQuery): Promise<CompanyNewsPayload> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), view });
  if (company.trim()) {
    params.set('company', company.trim());
  }
  return fetchPolledJson<CompanyNewsPayload>(`/api/opportunist-company-news?${params}`, 'analyzed company news');
}

function publishedTime(article: NewsArticle): number {
  return Date.parse(asUtcTimestamp(article.published_at));
}

function newestFirst(left: NewsArticle, right: NewsArticle): number {
  const leftTime = publishedTime(left);
  const rightTime = publishedTime(right);
  if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
    return rightTime - leftTime;
  }
  return right.article_id - left.article_id;
}

const STATUS_RANK: Record<NewsArticle['analysis_status'], number> = { pending: 0, processed_no_assessment: 1, analyzed: 2 };

function assessmentKey(assessment: ArticleAssessment): string {
  return [assessment.confidence, assessment.impact_direction, assessment.impact_magnitude, assessment.reason, assessment.created_at, assessment.news_scope].join('|');
}

/** One entry per article: repeats merge, keeping each assessment once and the most finished status. */
export function dedupeArticles(articles: NewsArticle[]): NewsArticle[] {
  const merged = new Map<number, { article: NewsArticle; keys: Set<string> }>();

  for (const article of articles) {
    const entry = merged.get(article.article_id) ?? { article: { ...article, assessments: [] }, keys: new Set<string>() };
    const status = article.analysis_status ?? 'pending';
    if (STATUS_RANK[status] > STATUS_RANK[entry.article.analysis_status ?? 'pending']) {
      entry.article.analysis_status = status;
    }
    for (const assessment of article.assessments) {
      const key = assessmentKey(assessment);
      if (!entry.keys.has(key)) {
        entry.keys.add(key);
        entry.article.assessments.push({ ...assessment });
      }
    }
    merged.set(article.article_id, entry);
  }

  return Array.from(merged.values(), (entry) => entry.article).sort(newestFirst);
}

function label(value: string, fallback: string): string {
  return formatText(value, { placeholder: fallback });
}

function card(id: string, cardLabel: string, title: string, subtitle: string, articles: NewsArticle[]): NewsCard {
  const deduped = dedupeArticles(articles);
  return { id, label: cardLabel, title, subtitle, latest_published_at: deduped[0]?.published_at ?? '', articles: deduped };
}

type Grouping = {
  key: (company: CompanyNewsEntry) => string;
  label: (company: CompanyNewsEntry) => string;
  title: (company: CompanyNewsEntry) => string;
  articles: (company: CompanyNewsEntry) => NewsArticle[];
  subtitle: (companyCount: number) => string;
};

/** Sector, industry and macro coverage, grouped across the companies on the loaded page. */
function groupedCards(companies: CompanyNewsEntry[], prefix: string, grouping: Grouping): NewsCard[] {
  const groups = new Map<string, { label: string; title: string; symbols: Set<string>; articles: NewsArticle[] }>();
  for (const company of companies) {
    const key = grouping.key(company) || company.symbol;
    const group = groups.get(key) ?? { label: grouping.label(company), title: grouping.title(company), symbols: new Set<string>(), articles: [] };
    group.symbols.add(company.symbol);
    group.articles.push(...grouping.articles(company));
    groups.set(key, group);
  }
  return Array.from(groups, ([key, group]) => card(`${prefix}-${key}`, group.label, group.title, grouping.subtitle(group.symbols.size), group.articles));
}

function sectorTitle(company: CompanyNewsEntry): string {
  return label(company.sector_name || company.sector_key, 'Unknown sector');
}

function loadedCompanies(count: number, kind: string): string {
  return `${formatNumber(count, { digits: 0 })} loaded companies in this ${kind}`;
}

export function buildCardsForView(companies: CompanyNewsEntry[], view: ScopeView): NewsCard[] {
  switch (view) {
    case 'company':
      return companies.map((company) => card(
        `company-${company.company_id}`,
        company.symbol,
        company.name,
        `${sectorTitle(company)} / ${label(company.industry_name || company.industry_key, 'Unknown industry')}`,
        company.company_news.articles,
      ));
    case 'sector':
      return groupedCards(companies, 'sector', {
        key: (company) => String(company.sector_id ?? company.sector_key ?? ''),
        label: (company) => label(company.sector_key, 'Sector'),
        title: sectorTitle,
        articles: (company) => company.sector_news.articles,
        subtitle: (count) => loadedCompanies(count, 'sector'),
      });
    case 'industry':
      return groupedCards(companies, 'industry', {
        key: (company) => String(company.industry_id ?? company.industry_key ?? ''),
        label: (company) => label(company.industry_key, 'Industry'),
        title: (company) => label(company.industry_name || company.industry_key, 'Unknown industry'),
        articles: (company) => company.industry_news.articles,
        subtitle: (count) => loadedCompanies(count, 'industry'),
      });
    case 'macro':
      return groupedCards(companies, 'macro', {
        key: (company) => String(company.sector_id ?? company.sector_key ?? ''),
        label: (company) => label(company.sector_key, 'Macro'),
        title: sectorTitle,
        articles: (company) => company.macro_news.articles,
        subtitle: () => 'Macro signals mapped to this sector',
      });
  }
}

/** One article in the feed, with the group it was loaded under. */
export type FeedRow = {
  key: string;
  label: string;
  title: string;
  article: NewsArticle;
  /** The article's assessments at the chosen confidence. */
  assessments: ArticleAssessment[];
};

function matches(row: FeedRow, needle: string): boolean {
  const fields = [row.label, row.title, row.article.title, row.article.source, ...row.assessments.map((assessment) => assessment.reason)];
  return fields.some((field) => field.toLowerCase().includes(needle));
}

/** Every group's articles as one feed, newest first, filtered by confidence and a search. */
export function feedRows(cards: NewsCard[], confidence: ConfidenceLevel, query: string): FeedRow[] {
  const needle = query.trim().toLowerCase();
  const rows: FeedRow[] = [];
  for (const group of cards) {
    for (const article of group.articles) {
      const assessments = confidence === 'all'
        ? article.assessments
        : article.assessments.filter((assessment) => assessment.confidence === confidence);
      if (confidence !== 'all' && assessments.length === 0) continue;
      const row = { key: `${group.id}-${article.article_id}`, label: group.label, title: group.title, article, assessments };
      if (!needle || matches(row, needle)) rows.push(row);
    }
  }
  return rows.sort((left, right) => newestFirst(left.article, right.article));
}
