import { useState } from 'react';

import { formatShortDateTime, formatText } from './format';
import { getNewsFeed, newsRows, visibleRows } from './liveFeeds';
import type { CompanyNewsFeed, NewsRow } from './liveFeeds';
import { DataTable } from './ui/DataTable';
import { Panel } from './ui/Panel';
import { SearchInput } from './ui/SearchInput';
import { TickerChip } from './ui/TickerChip';
import { usePolledResource } from './useRemoteResource';

const COLUMNS = [
  { key: 'time', header: 'Time', render: (row: NewsRow) => <span className="font-mono whitespace-nowrap text-dim">{formatShortDateTime(row.publishedAt)}</span> },
  { key: 'ticker', header: 'Ticker', render: (row: NewsRow) => <TickerChip symbol={row.ticker} /> },
  {
    key: 'headline',
    header: 'Headline',
    // A floor on the headline width makes the table scroll on phones instead of wrapping word by word.
    render: (row: NewsRow) => <a href={row.url} target="_blank" rel="noreferrer" className="block min-w-60 text-base-content hover:text-primary">{row.headline}</a>,
  },
  { key: 'source', header: 'Source', render: (row: NewsRow) => <span className="text-dim">{row.source}</span> },
  { key: 'confidence', header: 'Confidence', render: (row: NewsRow) => <span className="text-ink-2">{formatText(row.confidence, { placeholder: '—' })}</span> },
];

function searchFields(row: NewsRow): string[] {
  return [row.ticker, row.company, row.headline, row.source];
}

export function LiveNewsfeedView({ feed }: { feed: CompanyNewsFeed }) {
  const [query, setQuery] = useState('');
  const rows = visibleRows(newsRows(feed), query, searchFields);
  const empty = query.trim() ? (
    <>No articles match "{query.trim()}" in the loaded window · <a href={`#/decisions/news?q=${encodeURIComponent(query.trim())}`}>search all news</a></>
  ) : 'No articles yet.';

  return (
    <Panel
      title="Newsfeed"
      meta={`${feed.article_count.toLocaleString()} articles`}
      actions={<SearchInput label="Search news" value={query} onChange={setQuery} placeholder="Ticker, company, headline" />}
      flush
    >
      <DataTable columns={COLUMNS} rows={rows} rowKey={(row) => `${row.ticker}-${row.id}`} emptyMessage={empty} />
    </Panel>
  );
}

function LiveNewsfeed() {
  const { payload, error } = usePolledResource(getNewsFeed, { errorMessage: 'Failed to load company news.' });

  if (!payload) {
    return <Panel title="Newsfeed"><p className={error ? 'text-error' : 'text-dim'}>{error ?? 'Loading news…'}</p></Panel>;
  }
  return <LiveNewsfeedView feed={payload} />;
}

export default LiveNewsfeed;
