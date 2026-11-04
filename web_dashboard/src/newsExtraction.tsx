import { useMemo, useState } from 'react'

import PageHeader from './pageHeader'
import { chartTheme } from './ui/chartTheme'
import type { Column } from './ui/DataTable'
import { DataTable } from './ui/DataTable'
import { Panel } from './ui/Panel'
import { Select } from './ui/Select'
import { StatStrip } from './ui/StatStrip'
import { useFetchedResource } from './useRemoteResource'

/** The pass mark `scripts/check_extractors.py` uses for a domain. */
const PASS_RATE = 0.7
/** Below this many attempted urls a source is shown muted and never flagged. */
const SAMPLE_GUARDRAIL = 5
const EMPTY = '—'

type SourceRow = {
  source: string
  attempted: number
  succeeded: number
  failed: number
  permanent_failures: number
  success_rate: number
  top_error: string | null
}

type Payload = { days: number; start: string; end: string; sources: SourceRow[] }

function formatRate(rate: number | null): string {
  return rate === null ? EMPTY : `${Math.round(rate * 100)}%`
}

async function getNewsExtraction(days: number): Promise<Payload> {
  const response = await fetch(`/api/news-extraction?days=${days}`)
  const body = await response.json().catch(() => null) as Payload | { error?: string } | null
  if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to load the extraction report: ${response.status}`)
  return body as Payload
}

function RateBar({ row }: { row: SourceRow }) {
  const summary = `${row.source}: ${formatRate(row.success_rate)} of ${row.attempted} urls extracted (${row.succeeded} saved, ${row.failed} failed).`
  return <span className="relative block h-2.5 w-28 rounded-sm bg-base-300 sm:w-48" role="img" aria-label={summary} title={summary}>
    <span className="absolute inset-y-0 left-0 rounded-sm" style={{ width: `${row.success_rate * 100}%`, background: chartTheme.info }} />
    {/* The ring in the panel's colour keeps the pass tick readable over the fill. */}
    <span className="absolute -inset-y-1 -ml-px w-0.5 bg-base-content ring-1 ring-base-200" style={{ left: `${PASS_RATE * 100}%` }} />
  </span>
}

const WINDOW_OPTIONS = [
  { value: '1', label: 'Today' },
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
]

function measured(row: SourceRow): boolean {
  return row.attempted >= SAMPLE_GUARDRAIL
}

const COLUMNS: Column<SourceRow>[] = [
  { key: 'source', header: 'Source', render: (row) => <>
    <span className="font-medium">{row.source}</span>
    {measured(row) && row.success_rate < PASS_RATE ? <span className="block text-[11px] text-warning">Below pass mark</span> : null}
    {measured(row) ? null : <span className="block text-[11px] text-dim">below sample guardrail</span>}
  </> },
  { key: 'rate', header: 'Success rate', align: 'right', render: (row) => <>{formatRate(row.success_rate)}<span className="block text-[11px] text-dim">of {row.attempted}</span></> },
  { key: 'bar', header: '0–100%', render: (row) => <RateBar row={row} /> },
  { key: 'saved', header: 'Saved', align: 'right', render: (row) => row.succeeded },
  { key: 'failed', header: 'Failed', align: 'right', render: (row) => row.failed },
  { key: 'blocked', header: 'Blocked', align: 'right', render: (row) => row.permanent_failures },
  { key: 'error', header: 'Most common error', render: (row) => <span className="block max-w-72 whitespace-normal break-words text-[11px] text-dim">{row.top_error ?? EMPTY}</span> },
]

function NewsExtractionPage() {
  const [days, setDays] = useState(7)
  const load = useMemo(() => () => getNewsExtraction(days), [days])
  const { payload, error, isLoading } = useFetchedResource(load, { errorMessage: 'Failed to load the extraction report.' })

  const totals = useMemo(() => {
    const rows = payload?.sources ?? []
    const attempted = rows.reduce((sum, row) => sum + row.attempted, 0)
    const succeeded = rows.reduce((sum, row) => sum + row.succeeded, 0)
    return {
      attempted,
      succeeded,
      rate: attempted ? succeeded / attempted : null,
      permanent: rows.reduce((sum, row) => sum + row.permanent_failures, 0),
      failing: rows.filter((row) => measured(row) && row.success_rate < PASS_RATE).length,
      measured: rows.filter(measured).length,
    }
  }, [payload])

  return <main className="flex flex-col gap-3">
    <PageHeader
      title="Article extraction by source"
      description="How often a followed article link was saved rather than failing extraction, per source. Read-only."
      meta={payload ? `${payload.start} to ${payload.end} (UTC)` : null}
      actions={<Select label="Window" options={WINDOW_OPTIONS} value={String(days)} onChange={(value) => setDays(Number(value))} />}
    />
    {error ? <p role="alert" className="text-xs text-error">{error}</p> : null}
    {isLoading && !payload ? <p className="text-dim">Loading the extraction report…</p> : null}
    {payload ? <>
      <StatStrip items={[
        { label: 'Overall success rate', value: formatRate(totals.rate), sub: `${totals.succeeded} of ${totals.attempted} urls saved` },
        { label: `Sources below ${formatRate(PASS_RATE)}`, value: `${totals.failing} of ${totals.measured}`, sub: `with at least ${SAMPLE_GUARDRAIL} attempted urls` },
        { label: 'Blocked urls', value: totals.permanent, sub: 'HTTP 403 or 404, not retried' },
        { label: 'Sources seen', value: payload.sources.length, sub: 'with a saved or failed url' },
      ]} />
      <Panel title="Per source" meta={`tick = ${formatRate(PASS_RATE)} pass mark · sources with fewer than ${SAMPLE_GUARDRAIL} urls are muted`} flush>
        <DataTable label="Per source" columns={COLUMNS} rows={payload.sources} rowKey={(row) => row.source} rowClassName={(row) => (measured(row) ? '' : 'opacity-60')} emptyMessage="No article links were followed in this window." />
      </Panel>
      <p className="text-[11px] text-dim">Each url counts once. A url that failed and later succeeded counts as saved; failures are dated by their latest attempt. The same numbers come from <code className="font-mono">scripts/news_quality_report.py</code>.</p>
    </> : null}
  </main>
}

export default NewsExtractionPage
