import { formatCurrency, formatRate, formatToken } from './format'
import { getExecutionQuality } from './reportsApi'
import type { ExecutionQualityRow } from './reportsApi'
import type { Column } from './ui/DataTable'
import { DataTable } from './ui/DataTable'
import { Panel } from './ui/Panel'
import { useFetchedResource } from './useRemoteResource'

const COLUMNS: readonly Column<ExecutionQualityRow>[] = [
  { key: 'account', header: 'Account', render: (row) => (row.paper ? 'Paper' : 'Live') },
  // Older rows stored the SDK enum's name, e.g. "OrderSide.BUY".
  { key: 'side', header: 'Side', render: (row) => formatToken(row.order_side?.replace('OrderSide.', '').toLowerCase()) },
  { key: 'orders', header: 'Orders', align: 'right', render: (row) => row.submitted_orders },
  { key: 'full', header: 'Filled in full', align: 'right', render: (row) => formatRate(row.full_fill_rate) },
  { key: 'any', header: 'Any fill', align: 'right', render: (row) => formatRate(row.any_fill_rate) },
  { key: 'mid', header: 'Slippage vs mid', align: 'right', render: (row) => formatCurrency(row.mean_slippage_vs_mid) },
  { key: 'arrival', header: 'vs arrival', align: 'right', render: (row) => formatCurrency(row.mean_slippage_vs_arrival) },
  { key: 'spread', header: 'Spread paid', align: 'right', render: (row) => formatRate(row.mean_spread_paid_pct, { digits: 1 }) },
  { key: 'samples', header: 'Priced fills', align: 'right', render: (row) => row.slippage_samples },
]

/** How orders filled against the quote the bot decided on: per contract, in dollars. */
export function ExecutionQualityPanel() {
  const { payload, error } = useFetchedResource(getExecutionQuality, { errorMessage: 'Failed to load execution quality.' })

  return (
    <Panel title="Execution quality" meta={`last ${payload?.days ?? 30} days, per contract`} flush>
      {error ? <p className="p-3.5 text-error">{error}</p> : null}
      {payload ? (
        <DataTable label="Execution quality" columns={COLUMNS} rows={payload.rows}
          rowKey={(row) => `${row.paper}-${row.order_side}`} emptyMessage="No orders in this window." />
      ) : null}
      {!payload && !error ? <p className="p-3.5 text-dim">Loading…</p> : null}
    </Panel>
  )
}
