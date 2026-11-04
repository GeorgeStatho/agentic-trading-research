import { useState } from 'react';

import { formatShortTime, formatToken } from './format';
import { decisionRows, decisionSummary, getDecisionsFeed, visibleRows } from './liveFeeds';
import type { CompanyDecisionsFeed, DecisionRow } from './liveFeeds';
import { DataTable } from './ui/DataTable';
import { Panel } from './ui/Panel';
import { SearchInput } from './ui/SearchInput';
import { TickerChip } from './ui/TickerChip';
import { usePolledResource } from './useRemoteResource';

const COLUMNS = [
  { key: 'time', header: 'Time', render: (row: DecisionRow) => <span className="font-mono text-dim">{formatShortTime(row.updatedAt)}</span> },
  { key: 'ticker', header: 'Ticker', render: (row: DecisionRow) => <TickerChip symbol={row.ticker} /> },
  { key: 'strategist', header: 'Strategist', render: (row: DecisionRow) => formatToken(row.strategist, { placeholder: '—' }) },
  { key: 'manager', header: 'Manager', render: (row: DecisionRow) => formatToken(row.manager, { placeholder: '—' }) },
];

function searchFields(row: DecisionRow): string[] {
  return [row.ticker, row.company];
}

export function LiveDecisionsView({ feed }: { feed: CompanyDecisionsFeed }) {
  const [query, setQuery] = useState('');
  const rows = visibleRows(decisionRows(feed), query, searchFields);
  const empty = query.trim() ? (
    <>No decisions for "{query.trim()}" in the latest run · <a href={`#/decisions/ledger?q=${encodeURIComponent(query.trim())}`}>search all history</a></>
  ) : 'No decisions in the latest run.';

  return (
    <Panel
      title="Decisions"
      meta={decisionSummary(feed)}
      actions={<SearchInput label="Search decisions" value={query} onChange={setQuery} placeholder="Ticker or company" />}
      flush
    >
      <DataTable columns={COLUMNS} rows={rows} rowKey={(row) => row.id} emptyMessage={empty} />
    </Panel>
  );
}

function LiveDecisions() {
  const { payload, error } = usePolledResource(getDecisionsFeed, { errorMessage: 'Failed to load decisions.' });

  if (!payload) {
    return <Panel title="Decisions"><p className={error ? 'text-error' : 'text-dim'}>{error ?? 'Loading decisions…'}</p></Panel>;
  }
  return <LiveDecisionsView feed={payload} />;
}

export default LiveDecisions;
