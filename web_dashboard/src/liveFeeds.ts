import { fetchPolledJson } from './api';
import { asUtcTimestamp, formatToken } from './format';

/**
 * Rows for the Live page's compact Newsfeed and Decisions panels. Search is
 * client-side over what is loaded; the full pages search everything.
 */

export const FEED_ROWS = 8;

export type CompanyNewsFeed = {
  article_count: number;
  companies: Array<{
    symbol: string;
    name: string;
    company_news: {
      articles: Array<{
        article_id: number;
        published_at: string;
        title: string;
        source: string;
        source_url: string;
        assessments: Array<{ confidence: string }>;
      }>;
    };
  }>;
};

export type CompanyDecisionsFeed = {
  last_updated_at: string;
  companies: Array<{
    symbol: string;
    name: string;
    last_updated_at: string;
    strategist: { decision: string } | null;
    manager: { decision: string };
  }>;
};

export type NewsRow = {
  id: string;
  publishedAt: string;
  ticker: string;
  company: string;
  headline: string;
  source: string;
  url: string;
  confidence: string;
};

export type DecisionRow = {
  id: string;
  updatedAt: string;
  ticker: string;
  company: string;
  strategist: string;
  manager: string;
};

export function getNewsFeed(): Promise<CompanyNewsFeed> {
  return fetchPolledJson<CompanyNewsFeed>('/api/opportunist-company-news', 'company news');
}

export function getDecisionsFeed(): Promise<CompanyDecisionsFeed> {
  return fetchPolledJson<CompanyDecisionsFeed>('/api/company-decisions', 'company decisions');
}

function newestFirst(left: string, right: string): number {
  return Date.parse(asUtcTimestamp(right)) - Date.parse(asUtcTimestamp(left));
}

export function newsRows(feed: CompanyNewsFeed): NewsRow[] {
  return feed.companies
    .flatMap((company) => company.company_news.articles.map((article) => ({
      id: String(article.article_id),
      publishedAt: article.published_at,
      ticker: company.symbol,
      company: company.name,
      headline: article.title,
      source: article.source,
      url: article.source_url,
      confidence: article.assessments[0]?.confidence ?? '',
    })))
    .sort((left, right) => newestFirst(left.publishedAt, right.publishedAt));
}

export function decisionRows(feed: CompanyDecisionsFeed): DecisionRow[] {
  return feed.companies
    .map((company) => ({
      id: company.symbol,
      updatedAt: company.last_updated_at,
      ticker: company.symbol,
      company: company.name,
      strategist: company.strategist?.decision ?? '',
      manager: company.manager.decision,
    }))
    .sort((left, right) => newestFirst(left.updatedAt, right.updatedAt));
}

/** "59 in the latest run · 1 call · 58 no trade". */
export function decisionSummary(feed: CompanyDecisionsFeed): string {
  const count = (decision: string) => feed.companies.filter((company) => company.manager.decision === decision).length;
  const parts = [`${feed.companies.length} in the latest run`];
  for (const decision of ['call', 'put', 'neither']) {
    const n = count(decision);
    if (n > 0) {
      parts.push(`${n} ${formatToken(decision).toLowerCase()}`);
    }
  }
  return parts.join(' · ');
}

/** The newest rows with no search; every loaded match with one. */
export function visibleRows<Row>(rows: readonly Row[], query: string, fields: (row: Row) => string[]): Row[] {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return rows.slice(0, FEED_ROWS);
  }
  return rows.filter((row) => fields(row).some((field) => field.toLowerCase().includes(needle)));
}
