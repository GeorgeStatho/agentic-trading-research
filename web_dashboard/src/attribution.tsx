import { useState } from 'react'

import { formatDecimal, formatRate, formatToken } from './format'
import PageHeader from './pageHeader'
import { getDecisionAttribution, getExitAttribution } from './reportsApi'
import type { ExitAttributionRow, HitRateGroup } from './reportsApi'
import type { Column } from './ui/DataTable'
import { DataTable } from './ui/DataTable'
import { Panel } from './ui/Panel'
import { Segmented } from './ui/Segmented'
import { useFetchedResource } from './useRemoteResource'

const EXIT_COLUMNS: readonly Column<ExitAttributionRow>[] = [
  { key: 'reason', header: 'Exit reason', render: (row) => formatToken(row.exit_reason) },
  { key: 'closed', header: 'Closed', align: 'right', render: (row) => row.closed_positions },
  { key: 'paths', header: 'With price path', align: 'right', render: (row) => row.observed_path_count },
  { key: 'given', header: 'Median given back (pts)', align: 'right', render: (row) => formatDecimal(row.median_mfe_minus_realized_pct, { digits: 1 }) },
]

const HIT_RATE_COLUMNS: readonly Column<HitRateGroup>[] = [
  { key: 'group', header: 'Group', render: (row) => formatToken(row.key) },
  { key: 'count', header: 'Decisions', align: 'right', render: (row) => row.count },
  { key: 'graded', header: 'Graded', align: 'right', render: (row) => row.graded },
  {
    key: 'hit', header: 'Hit rate', align: 'right',
    render: (row) => row.hit_rate === null ? formatRate(null) : `${formatRate(row.hit_rate)} ±${formatRate(row.hit_rate_se)}`,
  },
]

const HORIZONS = [{ value: '5', label: '5 days' }, { value: '10', label: '10 days' }] as const
// One stable loader per horizon: useFetchedResource re-reads whenever the loader changes.
const ATTRIBUTION_LOADERS = { '5': () => getDecisionAttribution(5), '10': () => getDecisionAttribution(10) }

function HitRateTable({ label, rows }: { label: string; rows: HitRateGroup[] }) {
  return <DataTable label={label} columns={HIT_RATE_COLUMNS} rows={rows} rowKey={(row) => row.key} emptyMessage="No graded decisions yet." />
}

function AttributionPage() {
  const [horizon, setHorizon] = useState<'5' | '10'>('5')
  const exits = useFetchedResource(getExitAttribution, { errorMessage: 'Failed to load exit attribution.' })
  const decisions = useFetchedResource(ATTRIBUTION_LOADERS[horizon], { errorMessage: 'Failed to load decision attribution.' })
  const payload = decisions.payload

  return (
    <main className="flex flex-col gap-3">
      <PageHeader title="Attribution" description="Where exits leave money on the table, and whether the model's confidence and its evidence predict the call. Read-only." />
      <Panel title="Exit attribution" meta="closed positions, best price reached minus realized return" flush>
        {exits.error ? <p className="p-3.5 text-error">{exits.error}</p> : null}
        {exits.payload ? (
          <DataTable label="Exit attribution" columns={EXIT_COLUMNS} rows={exits.payload.rows} rowKey={(row) => row.exit_reason} emptyMessage="No closed positions yet." />
        ) : null}
      </Panel>
      <Panel
        title="Direction hit rate"
        meta={payload ? `${payload.total} decisions in the last ${payload.days} days, graded ${payload.horizon} trading days out` : undefined}
        actions={<Segmented label="Horizon" options={HORIZONS} value={horizon} onChange={setHorizon} />}
      >
        {decisions.error ? <p className="text-error">{decisions.error}</p> : null}
        {payload ? (
          <div className="grid gap-4 lg:grid-cols-3">
            <div><h3 className="mb-1 text-[11px] text-dim">By model self-rating</h3><HitRateTable label="By model self-rating" rows={payload.by_self_rating} /></div>
            <div><h3 className="mb-1 text-[11px] text-dim">By evidence quality</h3><HitRateTable label="By evidence quality" rows={payload.by_quality} /></div>
            <div><h3 className="mb-1 text-[11px] text-dim">By evidence source</h3><HitRateTable label="By evidence source" rows={payload.by_source} /></div>
          </div>
        ) : null}
        {!payload && !decisions.error ? <p className="text-dim">Loading…</p> : null}
      </Panel>
    </main>
  )
}

export default AttributionPage
