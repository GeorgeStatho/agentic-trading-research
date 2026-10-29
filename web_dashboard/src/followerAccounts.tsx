import { useMemo, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { formatDate, formatSignedCurrency, formatSignedPercent, formatToken } from './format'
import PageHeader from './pageHeader'
import { useFetchedResource } from './useRemoteResource'
import './decisionExpectancy.css'
import './followerAccounts.css'

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

type Measure = 'dollars' | 'return'

// Dark steps of the validated categorical palette, keyed by strategy so a line keeps its
// colour when the window changes which accounts are present. Production is the neutral baseline.
const STRATEGY_COLORS: Record<string, string> = {
  debit_spread: '#3987e5',
  itm_single: '#d95926',
  longer_dte: '#199e70',
  shares: '#c98500',
}
const SPARE_COLORS = ['#d55181', '#008300', '#9085e9', '#e66767']
const PRODUCTION_COLOR = '#c3c2b7'

function seriesColors(accounts: string[], payload: Payload): Record<string, string> {
  const colors: Record<string, string> = {}
  const used = new Set<string>()
  const spare = [...SPARE_COLORS]
  for (const account of [...accounts].sort()) {
    const summary = payload.accounts[account]
    if (summary?.role === 'primary') {
      colors[account] = PRODUCTION_COLOR
      continue
    }
    const preferred = STRATEGY_COLORS[summary?.strategy ?? '']
    const color = preferred && !used.has(preferred) ? preferred : spare.shift() ?? PRODUCTION_COLOR
    used.add(color)
    colors[account] = color
  }
  return colors
}

function formatMoney(value: number | null | undefined): string {
  return formatSignedCurrency(value ?? null, { placeholder: EMPTY })
}

function formatReturn(value: number | null | undefined): string {
  return formatSignedPercent(value ?? null, { placeholder: EMPTY, digits: 1 })
}

/** Average trade return minus production's on the same entries, in percentage points. */
function differenceFromProduction(entry: AccountSummary): number | null {
  const production = entry.production_avg_pnl_pct ?? null
  return entry.avg_pnl_pct === null || production === null ? null : entry.avg_pnl_pct - production
}

function SkippedCell({ skipped }: { skipped: Record<string, number> | undefined }) {
  const reasons = Object.entries(skipped ?? {})
  if (!reasons.length) return <>0</>
  return <>{reasons.map(([reason, count]) => <small key={reason}>{count} · {formatToken(reason)}</small>)}</>
}

function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return ''
  return value < 0 ? 'decision-expectancy__negative' : 'decision-expectancy__positive'
}

async function getFollowerAccounts(days: number): Promise<Payload> {
  const response = await fetch(`/api/follower-accounts?days=${days}`)
  const body = await response.json().catch(() => null) as Payload | { error?: string } | null
  if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to load follower accounts: ${response.status}`)
  return body as Payload
}

type ChartPoint = { at: number } & Record<string, number | null>

/** One point per closed trade; every account carries its running total forward. */
function cumulativeSeries(trades: Trade[], accounts: string[], measure: Measure): ChartPoint[] {
  const pnl: Record<string, number> = {}
  const capital: Record<string, number> = {}
  return trades.map((trade) => {
    pnl[trade.account] = (pnl[trade.account] ?? 0) + trade.pnl
    capital[trade.account] = (capital[trade.account] ?? 0) + trade.capital
    const point: ChartPoint = { at: new Date(trade.closed_at).getTime() }
    for (const account of accounts) {
      if (measure === 'dollars') point[account] = pnl[account] ?? 0
      else point[account] = capital[account] ? (100 * pnl[account]) / capital[account] : null
    }
    return point
  })
}

function CumulativeChart({ payload }: { payload: Payload }) {
  const [measure, setMeasure] = useState<Measure>('return')
  const accounts = useMemo(() => [...new Set(payload.trades.map((trade) => trade.account))], [payload])
  const colors = useMemo(() => seriesColors(accounts, payload), [accounts, payload])
  const data = useMemo(() => cumulativeSeries(payload.trades, accounts, measure), [payload, accounts, measure])
  const format = measure === 'dollars' ? formatMoney : formatReturn

  return <section className="decision-expectancy__table-card follower-accounts__chart-card">
    <header>
      <div>
        <p>Cumulative results</p>
        <h2>{measure === 'dollars' ? 'Realized P&L by close date' : 'Return on capital deployed, by close date'}</h2>
      </div>
      <div className="follower-accounts__toggle" role="group" aria-label="Chart measure">
        <button type="button" aria-pressed={measure === 'return'} onClick={() => setMeasure('return')}>Return on capital</button>
        <button type="button" aria-pressed={measure === 'dollars'} onClick={() => setMeasure('dollars')}>Dollars</button>
      </div>
    </header>
    {data.length ? <div className="follower-accounts__chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 16, right: 24, bottom: 4, left: 4 }}>
          <CartesianGrid stroke="#2d3449" strokeDasharray="4 5" vertical={false} />
          <XAxis dataKey="at" type="number" scale="time" domain={['dataMin', 'dataMax']}
            tickFormatter={(value: number) => formatDate(new Date(value).toISOString())}
            tick={{ fill: '#9ca3af', fontSize: 12 }} axisLine={{ stroke: '#3d4658' }} tickLine={false} />
          <YAxis width="auto" tickFormatter={(value: number) => format(value)}
            tick={{ fill: '#9ca3af', fontSize: 12 }} axisLine={false} tickLine={false} />
          <Tooltip
            labelFormatter={(value) => formatDate(new Date(Number(value)).toISOString())}
            formatter={(value) => format(typeof value === 'number' ? value : null)}
            cursor={{ stroke: '#5b6478', strokeWidth: 1 }}
            contentStyle={{ background: '#202838', border: '1px solid #3d4658', borderRadius: 10 }}
            labelStyle={{ color: '#f3f4f6' }} itemStyle={{ color: '#c4ccda' }} />
          <Legend wrapperStyle={{ color: '#c4ccda', fontSize: 12 }} />
          {accounts.map((account) => {
            const production = payload.accounts[account]?.role === 'primary'
            return <Line key={account} type="stepAfter" dataKey={account} name={production ? 'production (same entries)' : account}
              stroke={colors[account]} strokeWidth={2} strokeDasharray={production ? '6 4' : undefined}
              dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: '#171f33' }} connectNulls isAnimationActive={false} />
          })}
        </LineChart>
      </ResponsiveContainer>
    </div> : <p className="decision-expectancy__empty follower-accounts__inset">No follower trade has closed in this window yet.</p>}
  </section>
}

function StatsTable({ payload }: { payload: Payload }) {
  const rows = Object.entries(payload.accounts).sort(([, left], [, right]) =>
    Number(right.role === 'primary') - Number(left.role === 'primary'))
  return <section className="decision-expectancy__table-card">
    <header><div><p>Per account</p><h2>Each follower against production on the same entries</h2></div>
      <span>Accounts with fewer than {SAMPLE_GUARDRAIL} closed trades are muted</span></header>
    <div className="decision-expectancy__table-wrap"><table><thead><tr>
      <th>Account</th><th>Closed</th><th>Active</th><th>Failed</th><th>Skipped</th><th>Win rate</th><th>P&amp;L</th>
      <th>P&amp;L / capital</th><th>Avg trade</th><th>Production avg</th><th>Difference</th>
    </tr></thead><tbody>{rows.map(([name, entry]) => {
      const production = entry.role === 'primary'
      const difference = differenceFromProduction(entry)
      return <tr key={name} className={entry.closed < SAMPLE_GUARDRAIL ? 'decision-expectancy__muted' : ''}>
        <td><strong>{name}</strong><small>{formatToken(entry.strategy)} · {production ? 'primary' : 'follower'}</small></td>
        <td>{entry.closed}</td>
        <td>{production ? EMPTY : entry.active}</td>
        <td>{production ? EMPTY : entry.failed}</td>
        <td>{production ? EMPTY : <SkippedCell skipped={entry.skipped} />}</td>
        <td>{!production && entry.closed ? `${Math.round((100 * (entry.wins ?? 0)) / entry.closed)}%` : EMPTY}</td>
        <td className={pnlClass(entry.pnl)}>{production ? EMPTY : formatMoney(entry.pnl)}</td>
        <td className={pnlClass(entry.pnl)}>{!production && entry.capital ? formatReturn((100 * (entry.pnl ?? 0)) / entry.capital) : EMPTY}</td>
        <td className={pnlClass(entry.avg_pnl_pct)}>{formatReturn(entry.avg_pnl_pct)}</td>
        <td>{production ? EMPTY : formatReturn(entry.production_avg_pnl_pct)}</td>
        <td className={pnlClass(difference)}>{formatReturn(difference)}</td>
      </tr>
    })}</tbody></table></div>
  </section>
}

function RecentTrades({ trades }: { trades: Trade[] }) {
  const recent = trades.filter((trade) => trade.role === 'follower').slice(-RECENT_TRADES).reverse()
  return <section className="decision-expectancy__table-card">
    <header><div><p>Recent closed follower trades</p><h2>Each follower trade next to production's result on that entry</h2></div>
      <span>Latest {RECENT_TRADES}</span></header>
    {recent.length ? <div className="decision-expectancy__table-wrap"><table><thead><tr>
      <th>Account</th><th>Entry</th><th>Held</th><th>Exit</th><th>P&amp;L</th><th>Return</th><th>Production</th>
    </tr></thead><tbody>{recent.map((trade) => <tr key={`${trade.account}-${trade.source_order_id}`}>
      <td><strong>{trade.account}</strong><small>{formatToken(trade.strategy)}</small></td>
      <td>{trade.underlying ?? EMPTY}<small>{trade.decision ?? EMPTY}</small></td>
      <td>{formatDate(trade.opened_at ?? '', { placeholder: EMPTY })}<small>to {formatDate(trade.closed_at, { placeholder: EMPTY })}</small></td>
      <td>{formatToken(trade.exit_reason, { placeholder: EMPTY })}</td>
      <td className={pnlClass(trade.pnl)}>{formatMoney(trade.pnl)}</td>
      <td className={pnlClass(trade.pnl_pct)}>{formatReturn(trade.pnl_pct)}</td>
      <td className={pnlClass(trade.production_pnl_pct)}>{formatReturn(trade.production_pnl_pct)}</td>
    </tr>)}</tbody></table></div> : <p className="decision-expectancy__empty follower-accounts__inset">No follower trade has closed in this window yet.</p>}
  </section>
}

function FollowerAccountsPage() {
  const [days, setDays] = useState(0)
  const load = useMemo(() => () => getFollowerAccounts(days), [days])
  const { payload, error, isLoading: loading } = useFetchedResource(load, { errorMessage: 'Failed to load follower accounts.' })

  const followers = useMemo(() => Object.entries(payload?.accounts ?? {}).filter(([, entry]) => entry.role === 'follower'), [payload])
  const closedFollowerTrades = followers.reduce((total, [, entry]) => total + entry.closed, 0)
  const productionClosed = (payload?.trades ?? []).filter((trade) => trade.role === 'primary').length
  const best = useMemo(() => followers
    .filter(([, entry]) => entry.closed >= SAMPLE_GUARDRAIL)
    .map(([name, entry]) => ({ name, difference: differenceFromProduction(entry) }))
    .filter((candidate): candidate is { name: string; difference: number } => candidate.difference !== null)
    .sort((left, right) => right.difference - left.difference)[0], [followers])

  return <main className="decision-expectancy follower-accounts">
    <PageHeader
      title="Follower accounts"
      description="Each follower mirrors production's entries with a different vehicle on its own paper account, and exits when production does. Read-only."
      meta={payload ? `As of ${formatDate(payload.as_of, { placeholder: 'Unknown' })}` : null}
    />

    <section className="decision-expectancy__controls" aria-label="Follower filters">
      <label>Entries mirrored in<select value={days} onChange={(event) => setDays(Number(event.target.value))}>
        <option value={30}>30 days</option><option value={90}>90 days</option><option value={180}>180 days</option><option value={365}>365 days</option><option value={0}>All history</option>
      </select></label>
    </section>

    {error ? <section className="decision-expectancy__empty" role="alert">{error}</section> : null}
    {loading && !payload ? <section className="decision-expectancy__empty">Loading follower accounts…</section> : null}
    {payload ? followers.length ? <>
      <section className="decision-expectancy__summary" aria-label="Follower headline">
        <article><span>Follower accounts</span><b>{followers.length}</b><small>{followers.map(([name]) => name).join(', ')}</small></article>
        <article><span>Closed follower trades</span><b>{closedFollowerTrades}</b><small>Across {productionClosed} production entries that also closed</small></article>
        <article><span>Best against production</span><b className={pnlClass(best?.difference)}>{best ? formatReturn(best.difference) : EMPTY}</b>
          <small>{best ? `${best.name}, average trade vs production on the same entries` : `Needs ${SAMPLE_GUARDRAIL} closed trades in one account`}</small></article>
        <article><span>Production on mirrored entries</span><b className={pnlClass(payload.accounts.production?.avg_pnl_pct)}>{formatReturn(payload.accounts.production?.avg_pnl_pct)}</b>
          <small>{payload.accounts.production?.closed ?? 0} closed production trades</small></article>
      </section>
      <CumulativeChart payload={payload} />
      <StatsTable payload={payload} />
      <RecentTrades trades={payload.trades} />
      <p className="follower-accounts__footnote">Partial production exits are not mirrored: a follower closes in full when production fully closes.
        Production's dollars are its realized return on the premium it spent, the same budget each follower received.</p>
    </> : <section className="decision-expectancy__empty">No follower positions in this window. Configure accounts with <code>TRADING_ACCOUNTS</code> to start mirroring production.</section> : null}
  </main>
}

export default FollowerAccountsPage
