import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { formatDate, formatSignedCurrency, formatSignedPercent, formatToken } from './format'
import PageHeader from './pageHeader'
import { chartTheme } from './ui/chartTheme'
import { AXIS_LINE, AXIS_TICK, TOOLTIP_STYLE } from './ui/charts/axis'
import type { Column } from './ui/DataTable'
import { DataTable } from './ui/DataTable'
import { Panel } from './ui/Panel'
import { Select } from './ui/Select'
import { StatStrip } from './ui/StatStrip'
import type { Stat } from './ui/StatStrip'
import { TickerChip } from './ui/TickerChip'
import { useFetchedResource } from './useRemoteResource'

/**
 * Follower accounts against production on the same entries.
 *
 * Each follower mirrors production's entries with a different vehicle on its own
 * paper account. Production's dollars are its realized return applied to the
 * premium it spent, which is also the budget every follower was given.
 */

/** Matches the Expectancy and News lag pages: fewer closed trades than this are muted. */
const SAMPLE_GUARDRAIL = 5
const EMPTY = '—'
const RECENT_TRADES = 25

type AccountSummary = {
  strategy: string
  role: 'primary' | 'follower'
  closed: number
  active?: number
  failed?: number
  skipped?: Record<string, number>
  wins?: number
  pnl?: number
  capital?: number
  avg_pnl_pct: number | null
  production_avg_pnl_pct?: number | null
  production_pnl_on_capital_pct?: number | null
}

type Trade = {
  account: string
  strategy: string
  role: 'primary' | 'follower'
  underlying: string | null
  decision: string | null
  source_order_id: string
  opened_at: string | null
  closed_at: string
  exit_reason: string | null
  pnl: number
  capital: number
  pnl_pct: number
  production_pnl_pct: number | null
}

type Payload = {
  as_of: string
  days: number
  accounts: Record<string, AccountSummary>
  trades: Trade[]
}

type Account = [name: string, summary: AccountSummary]

// Keyed by strategy so an account keeps its colour when the window changes which accounts are present.
const STRATEGY_COLORS: Record<string, string> = {
  itm_single: chartTheme.info,
  longer_dte: chartTheme.accent,
  debit_spread: '#a371f7',
  shares: chartTheme.gain,
}
const SPARE_COLORS = ['#d55181', '#9085e9', '#e66767']

function seriesColors(accounts: readonly Account[]): Record<string, string> {
  const colors: Record<string, string> = {}
  const used = new Set<string>()
  const spare = [...SPARE_COLORS]
  for (const [name, summary] of accounts) {
    if (summary.role === 'primary') {
      colors[name] = chartTheme.ink
      continue
    }
    const preferred = STRATEGY_COLORS[summary.strategy]
    const color = preferred && !used.has(preferred) ? preferred : spare.shift() ?? chartTheme.neutral
    used.add(color)
    colors[name] = color
  }
  return colors
}

function formatMoney(value: number | null | undefined): string {
  return formatSignedCurrency(value ?? null, { placeholder: EMPTY })
}

function formatReturn(value: number | null | undefined): string {
  return formatSignedPercent(value ?? null, { placeholder: EMPTY, digits: 1 })
}

function formatPoints(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(1)} pts`
}

/** Average trade return minus production's on the same entries, in percentage points. */
function differenceFromProduction(entry: AccountSummary): number | null {
  const production = entry.production_avg_pnl_pct ?? null
  return entry.avg_pnl_pct === null || production === null ? null : entry.avg_pnl_pct - production
}

function returnOnCapital(entry: AccountSummary): number | null {
  return entry.capital ? (100 * (entry.pnl ?? 0)) / entry.capital : null
}

/** Total P/L over total capital minus production's on the same entries, in percentage points. */
function capitalDifferenceFromProduction(entry: AccountSummary): number | null {
  const own = returnOnCapital(entry)
  const production = entry.production_pnl_on_capital_pct ?? null
  return own === null || production === null ? null : own - production
}

function pointsCell(value: number | null): ReactNode {
  return <span className={pnlClass(value)}>{value === null ? EMPTY : formatPoints(value)}</span>
}

function returnTone(entry: AccountSummary | undefined): Stat['tone'] {
  if (!entry || entry.closed < SAMPLE_GUARDRAIL || entry.avg_pnl_pct === null) return 'muted'
  return entry.avg_pnl_pct < 0 ? 'loss' : 'gain'
}

function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return ''
  return value < 0 ? 'text-loss' : 'text-gain'
}

async function getFollowerAccounts(days: number): Promise<Payload> {
  const response = await fetch(`/api/follower-accounts?days=${days}`)
  const body = await response.json().catch(() => null) as Payload | { error?: string } | null
  if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to load follower accounts: ${response.status}`)
  return body as Payload
}

/** Production first, then followers by name. */
function orderedAccounts(payload: Payload): Account[] {
  return Object.entries(payload.accounts).sort(([leftName, left], [rightName, right]) =>
    Number(right.role === 'primary') - Number(left.role === 'primary') || leftName.localeCompare(rightName))
}

// ---- Charts -----------------------------------------------------------------

type EntryGroup = { label: string; returns: Record<string, number> }

/** One group per production entry, in the order they closed. */
function entryGroups(trades: readonly Trade[]): EntryGroup[] {
  const groups = new Map<string, EntryGroup>()
  for (const trade of trades) {
    const group = groups.get(trade.source_order_id) ?? { label: `${trade.underlying ?? '?'} ${trade.decision ?? ''}`.trim(), returns: {} }
    group.returns[trade.account] = trade.pnl_pct
    groups.set(trade.source_order_id, group)
  }
  return [...groups.values()]
}

function ReturnByVehicle({ trades, accounts, colors }: { trades: readonly Trade[]; accounts: readonly Account[]; colors: Record<string, string> }) {
  const groups = entryGroups(trades)
  const names = accounts.map(([name]) => name)
  const label = groups.map((group) => `${group.label}: ${names.filter((name) => name in group.returns).map((name) => `${name} ${formatReturn(group.returns[name])}`).join(', ')}`).join('; ')
  const data = groups.map((group) => ({ entry: group.label, ...group.returns }))
  return <div role="img" aria-label={`Return by vehicle, same entry: ${label}`} className="h-56">
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="20%">
        <CartesianGrid stroke={chartTheme.grid} vertical={false} />
        <XAxis dataKey="entry" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} />
        <YAxis width="auto" tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(value: number) => `${value}%`} />
        <ReferenceLine y={0} stroke={chartTheme.axis} />
        <Tooltip {...TOOLTIP_STYLE} cursor={{ fill: chartTheme.grid }} formatter={(value) => formatReturn(Number(value))} />
        {names.map((name) => <Bar key={name} dataKey={name} fill={colors[name]} isAnimationActive={false} />)}
      </BarChart>
    </ResponsiveContainer>
  </div>
}

function DifferenceBars({ followers }: { followers: readonly Account[] }) {
  const rows = followers
    .map(([name, entry]) => ({ name, entry, difference: differenceFromProduction(entry) }))
    .filter((row): row is { name: string; entry: AccountSummary; difference: number } => row.difference !== null)
    .sort((left, right) => right.difference - left.difference)
  const scale = Math.max(1, ...rows.map((row) => Math.abs(row.difference)))
  return <div role="img" aria-label={`Difference vs. production: ${rows.map((row) => `${row.name} ${formatPoints(row.difference)} (n=${row.entry.closed})`).join(', ')}`} className="flex flex-col gap-2">
    {rows.map((row) => <div key={row.name} className={`grid grid-cols-[minmax(6rem,10rem)_1fr_4.5rem] items-center gap-2 text-[11px] ${row.entry.closed < SAMPLE_GUARDRAIL ? 'opacity-60' : ''}`}>
      <span className="truncate text-ink-2">{row.name} <span className="text-dim">· {formatToken(row.entry.strategy)} · n={row.entry.closed}</span></span>
      <span className="relative h-3">
        <span className="absolute inset-y-0 left-1/2 w-px bg-base-300" />
        <span className={`absolute inset-y-0 rounded-sm ${row.difference < 0 ? 'right-1/2 bg-loss' : 'left-1/2 bg-gain'}`} style={{ width: `${(Math.abs(row.difference) / scale) * 50}%` }} />
      </span>
      <span className={`text-right ${pnlClass(row.difference)}`}>{formatPoints(row.difference)}</span>
    </div>)}
  </div>
}

// ---- Tables -----------------------------------------------------------------

function skippedText(skipped: Record<string, number> | undefined): string {
  const reasons = Object.entries(skipped ?? {})
  return reasons.length ? reasons.map(([reason, count]) => `${count} · ${formatToken(reason)}`).join(', ') : '0'
}

function followerOnly(render: (entry: AccountSummary) => ReactNode) {
  return ([, entry]: Account) => (entry.role === 'primary' ? EMPTY : render(entry))
}

const ACCOUNT_COLUMNS: Column<Account>[] = [
  { key: 'account', header: 'Account', render: ([name, entry]) => <><span className="font-medium">{name}</span><span className="block text-[11px] text-dim">{formatToken(entry.strategy)} · {entry.role === 'primary' ? 'primary' : 'follower'}</span></> },
  { key: 'closed', header: 'Closed', align: 'right', render: ([, entry]) => entry.closed },
  { key: 'active', header: 'Active', align: 'right', render: ([, entry]) => entry.active ?? EMPTY },
  { key: 'failed', header: 'Failed', align: 'right', render: followerOnly((entry) => entry.failed) },
  { key: 'skipped', header: 'Skipped', render: followerOnly((entry) => skippedText(entry.skipped)) },
  { key: 'win', header: 'Win rate', align: 'right', render: ([, entry]) => (entry.closed && entry.wins !== undefined ? `${Math.round((100 * entry.wins) / entry.closed)}%` : EMPTY) },
  { key: 'pnl', header: 'P/L', align: 'right', render: ([, entry]) => <span className={pnlClass(entry.pnl)}>{formatMoney(entry.pnl)}</span> },
  { key: 'pnl-capital', header: 'P/L / capital', align: 'right', render: ([, entry]) => <span className={pnlClass(entry.pnl)}>{formatReturn(returnOnCapital(entry))}</span> },
  { key: 'production-capital', header: 'Production P/L / capital', align: 'right', render: followerOnly((entry) => formatReturn(entry.production_pnl_on_capital_pct)) },
  { key: 'capital-difference', header: 'Capital diff', align: 'right', render: ([, entry]) => pointsCell(capitalDifferenceFromProduction(entry)) },
  { key: 'avg', header: 'Avg trade', align: 'right', render: ([, entry]) => <span className={pnlClass(entry.avg_pnl_pct)}>{formatReturn(entry.avg_pnl_pct)}</span> },
  { key: 'production', header: 'Production avg', align: 'right', render: followerOnly((entry) => formatReturn(entry.production_avg_pnl_pct)) },
  { key: 'difference', header: 'Avg diff', align: 'right', render: ([, entry]) => pointsCell(differenceFromProduction(entry)) },
]

const TRADE_COLUMNS: Column<Trade>[] = [
  { key: 'account', header: 'Account', render: (trade) => <><span className="font-medium">{trade.account}</span><span className="block text-[11px] text-dim">{formatToken(trade.strategy)}</span></> },
  { key: 'entry', header: 'Entry', render: (trade) => <>{trade.underlying ? <TickerChip symbol={trade.underlying} /> : EMPTY}<span className="block text-[11px] text-dim">{trade.decision ?? EMPTY}</span></> },
  { key: 'held', header: 'Held', render: (trade) => <span className="whitespace-nowrap">{formatDate(trade.opened_at ?? '', { placeholder: EMPTY })} → {formatDate(trade.closed_at, { placeholder: EMPTY })}</span> },
  { key: 'exit', header: 'Exit', render: (trade) => formatToken(trade.exit_reason, { placeholder: EMPTY }) },
  { key: 'pnl', header: 'P/L', align: 'right', render: (trade) => <span className={pnlClass(trade.pnl)}>{formatMoney(trade.pnl)}</span> },
  { key: 'return', header: 'Return', align: 'right', render: (trade) => <span className={pnlClass(trade.pnl_pct)}>{formatReturn(trade.pnl_pct)}</span> },
  { key: 'production', header: 'Production', align: 'right', render: (trade) => <span className={pnlClass(trade.production_pnl_pct)}>{formatReturn(trade.production_pnl_pct)}</span> },
]

// ---- Page -------------------------------------------------------------------

const WINDOW_OPTIONS = [
  { value: '0', label: 'All history' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '180', label: '180 days' },
  { value: '365', label: '365 days' },
]

function FollowerView({ payload }: { payload: Payload }) {
  const accounts = orderedAccounts(payload)
  const followers = accounts.filter(([, entry]) => entry.role === 'follower')
  const colors = seriesColors(accounts)
  const production = payload.accounts.production
  const closedFollowerTrades = followers.reduce((total, [, entry]) => total + entry.closed, 0)
  const productionEntries = payload.trades.filter((trade) => trade.role === 'primary').length
  const skipped = followers.flatMap(([, entry]) => Object.entries(entry.skipped ?? {}))
  const failed = followers.reduce((total, [, entry]) => total + (entry.failed ?? 0), 0)
  const best = followers
    .filter(([, entry]) => entry.closed >= SAMPLE_GUARDRAIL)
    .map(([name, entry]) => ({ name, difference: differenceFromProduction(entry) }))
    .filter((candidate): candidate is { name: string; difference: number } => candidate.difference !== null)
    .sort((left, right) => right.difference - left.difference)[0]
  const recent = payload.trades.filter((trade) => trade.role === 'follower').slice(-RECENT_TRADES).reverse()

  return <>
    <StatStrip items={[
      { label: 'Follower accounts', value: followers.length, sub: followers.map(([name]) => name).join(' · ') },
      { label: 'Closed follower trades', value: closedFollowerTrades, sub: `across ${productionEntries} production entries` },
      { label: 'Production, mirrored entries', value: formatReturn(production?.avg_pnl_pct), sub: `avg of ${production?.closed ?? 0} closed trades`, tone: returnTone(production) },
      { label: 'Best vs. production', value: best ? formatPoints(best.difference) : EMPTY, sub: best ? `${best.name}, avg trade on the same entries` : `needs ${SAMPLE_GUARDRAIL} closed trades in one account`, tone: best ? undefined : 'muted' },
      { label: 'Skipped / failed', value: `${skipped.reduce((total, [, count]) => total + count, 0)} / ${failed}`, sub: skipped.map(([reason]) => formatToken(reason).toLowerCase()).join(', ') || undefined },
    ]} />
    <div className="flex flex-wrap gap-3">
      <Panel
        className="min-w-72 flex-[3_1_28rem]"
        title="Return by vehicle, same entry"
        actions={<div className="flex flex-wrap gap-x-3 gap-y-1">{accounts.map(([name]) => <span key={name} className="flex items-center gap-1.5 text-[11px] text-dim"><i className="size-2.5 rounded-sm" style={{ background: colors[name] }} />{name}</span>)}</div>}
      >
        {payload.trades.length ? <ReturnByVehicle trades={payload.trades} accounts={accounts} colors={colors} /> : <p className="text-dim">No follower trade has closed in this window yet.</p>}
      </Panel>
      <Panel className="min-w-72 flex-[2_1_20rem]" title="Difference vs. production" meta={followers.every(([, entry]) => entry.closed < SAMPLE_GUARDRAIL) ? `all below ${SAMPLE_GUARDRAIL} closed trades` : undefined}>
        <DifferenceBars followers={followers} />
        <p className="mt-2 text-[11px] text-dim">Average trade return minus production's on the same entries, in percentage points.</p>
      </Panel>
    </div>
    <Panel title="Per account" meta={`accounts with fewer than ${SAMPLE_GUARDRAIL} closed trades are muted`} flush>
      <DataTable label="Per account" columns={ACCOUNT_COLUMNS} rows={accounts} rowKey={([name]) => name} rowClassName={([, entry]) => (entry.closed < SAMPLE_GUARDRAIL ? 'opacity-60' : '')} />
    </Panel>
    <Panel title="Recent closed follower trades" meta={`latest ${RECENT_TRADES}`} flush>
      <DataTable label="Recent closed follower trades" columns={TRADE_COLUMNS} rows={recent} rowKey={(trade) => `${trade.account}-${trade.source_order_id}`} emptyMessage="No follower trade has closed in this window yet." />
    </Panel>
    <p className="text-[11px] text-dim">Both differences compare a follower with production on the same entries, in percentage points: Capital diff uses total P/L over total capital, so bigger positions count more; Avg diff averages trade returns, so every trade counts once.</p>
    <p className="text-[11px] text-dim">Partial production exits are not mirrored: a follower closes in full when production fully closes. Production's dollars are its realized return on the premium it spent, the same budget each follower received.</p>
  </>
}

function FollowerAccountsPage() {
  const [days, setDays] = useState(0)
  const load = useMemo(() => () => getFollowerAccounts(days), [days])
  const { payload, error, isLoading } = useFetchedResource(load, { errorMessage: 'Failed to load follower accounts.' })
  const hasFollowers = Object.values(payload?.accounts ?? {}).some((entry) => entry.role === 'follower')

  return <main className="flex flex-col gap-3">
    <PageHeader
      title="Follower accounts"
      description="Each follower mirrors production's entries with a different vehicle on its own paper account, and exits when production does. Read-only."
      meta={payload ? `As of ${formatDate(payload.as_of, { placeholder: 'Unknown' })}` : null}
      actions={<Select label="Entries mirrored in" options={WINDOW_OPTIONS} value={String(days)} onChange={(value) => setDays(Number(value))} />}
    />
    {error ? <p role="alert" className="text-xs text-error">{error}</p> : null}
    {isLoading && !payload ? <p className="text-dim">Loading follower accounts…</p> : null}
    {payload && hasFollowers ? <FollowerView payload={payload} /> : null}
    {payload && !hasFollowers ? <p className="text-dim">No follower positions in this window. Configure accounts with <code className="font-mono">TRADING_ACCOUNTS</code> to start mirroring production.</p> : null}
  </main>
}

export default FollowerAccountsPage
