import { useCallback, useState } from 'react';

import { buildCardsForView, feedRows, getCompanyNews } from './companyNews';
import type { ConfidenceLevel, FeedRow, NewsArticle, NewsQuery, ScopeView } from './companyNews';
import { formatDecimal, formatNumber, formatShortDateTime, sentenceCase } from './format';
import PageHeader from './pageHeader';
import { searchFromHash } from './routes';
import { Panel } from './ui/Panel';
import { SearchInput } from './ui/SearchInput';
import { Segmented } from './ui/Segmented';
import { Select } from './ui/Select';
import { Tag } from './ui/Tag';
import { TickerChip } from './ui/TickerChip';
import { usePolledResource } from './useRemoteResource';

/** Rows shown at a time: a page of companies can carry a thousand articles. */
const ROW_STEP = 50;

const SCOPES: { value: ScopeView; label: string }[] = [
  { value: 'company', label: 'Company' },
  { value: 'sector', label: 'Sector' },
  { value: 'industry', label: 'Industry' },
  { value: 'macro', label: 'Macro' },
];

const CONFIDENCE_OPTIONS: { value: ConfidenceLevel; label: string }[] = [
  { value: 'all', label: 'Any' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
];

const PAGE_SIZE_OPTIONS = ['3', '5', '10', '15'].map((value) => ({ value, label: value }));

const STATUS_TEXT: Record<NewsArticle['analysis_status'], string> = {
  analyzed: 'Analyzed',
  processed_no_assessment: 'No material impact',
  pending: 'Pending analysis',
};

function count(value: number): string {
  return formatNumber(value, { digits: 0 });
}

function companies(value: number): string {
  return `${count(value)} ${value === 1 ? 'company' : 'companies'}`;
}

/** The article's evidence score (0–1) as a short bar with the number beside it. */
function EvidenceBar({ score }: { score: number }) {
  const label = formatDecimal(score);
  return (
    <span role="img" aria-label={`Evidence score ${label}`} title="Evidence score" className="flex items-center gap-1 font-mono text-[11px] text-dim">
      <span className="h-1.5 w-10 rounded-sm bg-base-300"><span className="block h-full rounded-sm bg-info" style={{ width: `${Math.min(1, Math.max(0, score)) * 100}%` }} /></span>
      {label}
    </span>
  );
}

function ArticleRow({ row }: { row: FeedRow }) {
  const { article } = row;
  const title = article.title || 'Untitled article';
  return (
    <li className="flex flex-col gap-1 px-3.5 py-2">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-mono text-[11px] text-dim">{formatShortDateTime(article.published_at, { placeholder: 'Unknown time' })}</span>
        <TickerChip symbol={row.label} />
        {article.source_url
          ? <a href={article.source_url} target="_blank" rel="noreferrer" className="min-w-48 flex-1 font-medium text-base-content hover:text-primary">{title}</a>
          : <span className="min-w-48 flex-1 font-medium">{title}</span>}
        <span className="text-[11px] text-dim">{article.source}</span>
        {article.evidence_score != null ? <EvidenceBar score={article.evidence_score} /> : null}
      </div>
      {row.assessments.length ? row.assessments.map((assessment, index) => (
        <div key={index} className="flex flex-wrap items-baseline gap-x-2 text-xs">
          <Tag>{sentenceCase(assessment.confidence || 'unrated')}</Tag>
          {assessment.impact_direction || assessment.impact_magnitude ? (
            <span className="text-dim">{[assessment.impact_direction, assessment.impact_magnitude].filter(Boolean).join(' · ')}</span>
          ) : null}
          <span className="min-w-48 flex-1 text-ink-2">{assessment.reason || 'No reason recorded.'}</span>
        </div>
      )) : (
        <div className="text-xs"><Tag>{STATUS_TEXT[article.analysis_status]}</Tag></div>
      )}
    </li>
  );
}

function AnalyzedCompanyNewsPage() {
  const [initialQuery] = useState(() => searchFromHash(window.location.hash));
  const [search, setSearch] = useState(initialQuery);
  const [newsQuery, setNewsQuery] = useState<NewsQuery>({ page: 1, pageSize: 5, view: 'company', company: initialQuery });
  const [confidence, setConfidence] = useState<ConfidenceLevel>('all');
  const [shown, setShown] = useState(ROW_STEP);

  const load = useCallback(() => getCompanyNews(newsQuery), [newsQuery]);
  const { payload, error } = usePolledResource(load, { errorMessage: 'Failed to load analyzed company news.' });

  function ask(change: Partial<NewsQuery>) {
    setNewsQuery((current) => ({ ...current, ...change }));
    setShown(ROW_STEP);
  }

  const rows = payload ? feedRows(buildCardsForView(payload.companies, newsQuery.view), confidence, search) : [];
  const remaining = rows.length - shown;
  const scopes = SCOPES.map((scope) => ({
    value: scope.value,
    label: payload
      ? <>{scope.label} <span className="font-normal text-dim">{count(payload.section_article_counts[scope.value])}</span></>
      : scope.label,
  }));

  return (
    <main className="flex flex-col gap-3">
      <PageHeader
        title="Company news"
        description="Every article the opportunist analyzed, by company, sector, industry or macro scope. Read-only; refreshes every minute."
        meta={payload ? `${companies(payload.company_count)} · ${count(payload.article_count)} articles in this scope · updated ${formatShortDateTime(payload.as_of)}` : undefined}
      />
      <form
        role="search"
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          ask({ company: search, page: 1 });
        }}
      >
        <SearchInput label="Search news" showLabel value={search} onChange={setSearch} placeholder="Filter, or Enter to look up a company" />
        <div className="flex flex-col gap-1">
          <span className="text-[11px] text-dim">Scope</span>
          <Segmented label="Scope" options={scopes} value={newsQuery.view} onChange={(view) => ask({ view })} />
        </div>
        <Select label="Confidence" options={CONFIDENCE_OPTIONS} value={confidence} onChange={setConfidence} />
        <Select label="Companies per page" options={PAGE_SIZE_OPTIONS} value={String(newsQuery.pageSize)} onChange={(size) => ask({ pageSize: Number(size), page: 1 })} />
      </form>
      {payload?.lookup_active ? (
        <p className="text-xs text-ink-2">
          {companies(payload.lookup_match_count)} {payload.lookup_match_count === 1 ? 'matches' : 'match'} "{payload.lookup_query}" ·{' '}
          <button type="button" className="link" onClick={() => { setSearch(''); ask({ company: '', page: 1 }); }}>clear lookup</button>
        </p>
      ) : null}
      {payload ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
          <span>Companies {count(payload.page_start)}–{count(payload.page_end)} of {count(payload.lookup_active ? payload.lookup_match_count : payload.company_count)} · page {payload.page} of {payload.total_pages || 1}</span>
          <button type="button" className="btn btn-xs" aria-label="Previous page" disabled={!payload.has_previous_page} onClick={() => ask({ page: payload.page - 1 })}>Previous</button>
          <button type="button" className="btn btn-xs" aria-label="Next page" disabled={!payload.has_next_page} onClick={() => ask({ page: payload.page + 1 })}>Next</button>
        </div>
      ) : null}
      <Panel title="Articles" meta={payload ? `${count(rows.length)} on this page` : undefined} flush>
        {!payload ? <p className={`p-3.5 ${error ? 'text-error' : 'text-dim'}`}>{error ?? 'Loading analyzed news…'}</p> : null}
        {payload && rows.length === 0 ? <p className="p-3.5 text-dim">No articles match these filters.</p> : null}
        {rows.length ? (
          <ul aria-label="Articles" className="divide-y divide-raised">
            {rows.slice(0, shown).map((row) => <ArticleRow key={row.key} row={row} />)}
          </ul>
        ) : null}
        {remaining > 0 ? (
          <div className="border-t border-base-300 p-2 text-center">
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => setShown((current) => current + ROW_STEP)}>
              Show {Math.min(ROW_STEP, remaining)} more
            </button>
          </div>
        ) : null}
      </Panel>
    </main>
  );
}

export default AnalyzedCompanyNewsPage;
