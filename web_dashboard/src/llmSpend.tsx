import { formatCurrency, formatNumber, formatToken } from './format'
import { getLlmUsage } from './reportsApi'
import type { UsageGroup } from './reportsApi'
import type { Column } from './ui/DataTable'
import { DataTable } from './ui/DataTable'
import { Panel } from './ui/Panel'
import { StatStrip } from './ui/StatStrip'
import { useFetchedResource } from './useRemoteResource'

function columns(header: string, label: (key: string) => string): readonly Column<UsageGroup>[] {
  return [
    { key: 'key', header, render: (row) => label(row.key) },
    { key: 'calls', header: 'Calls', align: 'right', render: (row) => formatNumber(row.calls, { digits: 0 }) },
    { key: 'tokens', header: 'Tokens in / out', align: 'right', render: (row) => `${formatNumber(row.prompt_tokens, { digits: 0 })} / ${formatNumber(row.completion_tokens, { digits: 0 })}` },
    { key: 'cost', header: 'Cost', align: 'right', render: (row) => formatCurrency(row.cost_usd) },
  ]
}

const STAGE_COLUMNS = columns('Stage', (key) => (key === 'unknown' ? 'Unknown (before stages were recorded)' : formatToken(key)))
const PROVIDER_COLUMNS = columns('Model · provider', (key) => key)
const DAY_COLUMNS = columns('Day', (key) => key)

function UsageTable({ label, rows, columns: tableColumns }: { label: string; rows: UsageGroup[]; columns: readonly Column<UsageGroup>[] }) {
  return (
    <div className="min-w-0">
      <h3 className="mb-1 text-[11px] text-dim">{label}</h3>
      <DataTable label={label} columns={tableColumns} rows={rows} rowKey={(row) => row.key} emptyMessage="No calls in this window." />
    </div>
  )
}

/** What the hosted models cost, as OpenRouter reported it per call. */
export function LlmSpendPanel() {
  const { payload, error } = useFetchedResource(getLlmUsage, { errorMessage: 'Failed to load LLM usage.' })
  const totals = payload?.totals

  return (
    <Panel title="LLM spend" meta="hosted calls, cost as the provider reported it">
      {error ? <p className="text-error">{error}</p> : null}
      {payload && totals ? (
        <div className="flex flex-col gap-3">
          <StatStrip items={[
            { label: `Last ${payload.days} days`, value: formatCurrency(totals.cost_usd), sub: `${formatNumber(totals.calls, { digits: 0 })} calls` },
            { label: 'Last 7 days', value: formatCurrency(payload.last_7_days_cost_usd) },
            { label: 'Per call', value: formatCurrency(totals.calls ? totals.cost_usd / totals.calls : null, { digits: 4 }) },
            { label: 'Reasoning tokens', value: formatNumber(totals.reasoning_tokens, { digits: 0 }), sub: 'part of tokens out' },
          ]} />
          <div className="grid gap-4 lg:grid-cols-3">
            <UsageTable label="Spend by stage" rows={payload.by_stage} columns={STAGE_COLUMNS} />
            <UsageTable label="Spend by provider" rows={payload.by_provider} columns={PROVIDER_COLUMNS} />
            <UsageTable label="Spend by day" rows={payload.by_day} columns={DAY_COLUMNS} />
          </div>
          {payload.by_model.length > 1 ? <UsageTable label="Spend by model" rows={payload.by_model} columns={PROVIDER_COLUMNS} /> : null}
        </div>
      ) : null}
      {!payload && !error ? <p className="text-dim">Loading…</p> : null}
    </Panel>
  )
}
