import { useMemo, useState } from 'react'

import { formatDate, formatSignedPercent, formatToken } from './format'
import PageHeader from './pageHeader'
import { chartTheme } from './ui/chartTheme'
import { LagScatter } from './ui/charts/LagScatter'
import type { Column } from './ui/DataTable'
import { DataTable } from './ui/DataTable'
import { Panel } from './ui/Panel'
import { Select } from './ui/Select'
import { StatStrip } from './ui/StatStrip'
import { TickerChip } from './ui/TickerChip'
import { useFetchedResource } from './useRemoteResource'

/** Matches the Expectancy page: below this many closed trades a cohort is shown muted. */
const SAMPLE_GUARDRAIL = 5
const EMPTY = '—'

type Bucket = '<1h' | '1-6h' | 'overnight' | '>1d' | 'unknown'

type Trade = {
  order_id: string
  symbol: string
  direction: 'call' | 'put' | null
  ordered_at: string | null
  lag_hours: number | null
  lag_bucket: Bucket
  pre_entry_move_pct: number | null
  post_entry_move_pct: number | null
  pre_entry_share: number | null
  closed: boolean
  option_pnl_pct: number | null
  favourable_but_lost: boolean
  exit_reason: string | null
}

type Stats = { n: number; mean: number | null; median: number | null }

type BucketSummary = {
  trades: number
  closed: number
  option_pnl_pct: Stats & { win_rate: number | null; total: number | null }
  pre_entry_move_pct: Stats
  post_entry_move_pct: Stats
  pre_entry_share: Stats
  daily_sampled_publish_prices: number
}

type Payload = {
  as_of: string
  headline: {
    entries: number
    dated_entries: number
    median_lag_hours: number | null
    within_1h: number
    median_pre_entry_share: number | null
    favourable_closed: number
    favourable_but_lost: number
    measurable: number
  }
  buckets: Record<Bucket, BucketSummary>
  all: BucketSummary
  coverage: { entries: number; missing: Record<string, number>; future_dated_citations: number }
  trades: Trade[]
}

const BUCKET_LABELS: Record<Bucket, string> = {
  '<1h': 'Under 1 hour',
  '1-6h': '1–6 hours',
  overnight: 'Overnight (6–24 h)',
  '>1d': 'Over 1 day',
  unknown: 'No dated citation',
}

const MISSING_LABELS: Record<string, string> = {
  no_citations: 'decision predates article citations',
  no_decision_link: 'order has no linked decision',
  no_publish_time_before_order: 'no cited article published before the order',
  no_publish_price: 'no stock price at publish time',
  publish_and_entry_same_bar: 'publish and entry fall in the same price bar',
  entry_and_exit_same_bar: 'entry and exit fall in the same price bar',
  no_entry_price: 'no stock price at entry',
  no_exit_price: 'no stock price at exit',
  open_or_no_pnl: 'still open or no realized P&L',
  no_direction: 'call/put direction unknown',
}

function formatLag(hours: number | null): string {
  if (hours === null) return EMPTY
  return hours < 24 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`
}

function formatShare(share: number | null): string {
  return share === null ? EMPTY : `${Math.round(share * 100)}%`
}

function formatMove(value: number | null): string {
  return formatSignedPercent(value, { placeholder: EMPTY })
}

function pnlClass(value: number | null): string {
  if (value === null || value === 0) return ''
  return value < 0 ? 'text-loss' : 'text-gain'
}

async function getNewsLag(days: number): Promise<Payload> {
  const response = await fetch(`/api/news-lag?days=${days}`)
  const body = await response.json().catch(() => null) as Payload | { error?: string } | null
  if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to load the news-lag report: ${response.status}`)
  return body as Payload
}

// ---- Move-split bar ---------------------------------------------------------
// Plain HTML positioned in percent, so the bar can narrow on phones without shrinking its axis text.

const PATH_COLORS = { before: chartTheme.neutral, ourWay: chartTheme.info, againstUs: chartTheme.accent }

type Scale = { min: number; max: number; x: (value: number) => number }

function makeScale(trades: Trade[]): Scale {
  const ends = trades.flatMap((trade) => {
    const pre = trade.pre_entry_move_pct ?? 0
    return [pre, pre + (trade.post_entry_move_pct ?? 0)]
  })
  // Whole-percent bounds that always include zero, so the axis reads cleanly.
  const min = Math.min(-1, Math.floor(Math.min(0, ...ends)))
  const max = Math.max(1, Math.ceil(Math.max(0, ...ends)))
  return { min, max, x: (value) => ((value - min) / (max - min)) * 100 }
}

/** A segment from `from` to `to` (in percent of the track), rounded only at its data end. */
function Segment({ from, to, color }: { from: number; to: number; color: string }) {
  if (Math.abs(to - from) < 0.2) return null
  return <span
    className={`absolute top-[9px] h-2.5 ${to >= from ? 'rounded-r' : 'rounded-l'}`}
    style={{ left: `${Math.min(from, to)}%`, width: `${Math.abs(to - from)}%`, background: color }}
  />
}

function moveSummary(trade: Trade): string {
  const before = trade.pre_entry_move_pct === null ? 'before entry not measured' : `${formatMove(trade.pre_entry_move_pct)} before entry`
  const after = trade.post_entry_move_pct === null ? (trade.closed ? 'after entry not measured' : 'still open') : `${formatMove(trade.post_entry_move_pct)} after entry`
  return `${trade.symbol}: stock moved ${before}, ${after} (in the trade's direction).`
}

function MoveSplit({ trade, scale }: { trade: Trade; scale: Scale }) {
  const pre = trade.pre_entry_move_pct
  const post = trade.post_entry_move_pct
  const entry = pre ?? 0
  const summary = moveSummary(trade)
  // The ring in the panel's colour separates the entry marker from both segments.
  const marker = pre === null ? 'bg-base-200 border-[1.5px] border-dim' : 'bg-base-content'
  return <span className="relative block h-7 w-36 sm:w-60" role="img" aria-label={summary} title={summary}>
    <span className="absolute inset-y-0.5 w-px bg-base-300" style={{ left: `${scale.x(0)}%` }} />
    {pre !== null ? <Segment from={scale.x(0)} to={scale.x(pre)} color={PATH_COLORS.before} /> : null}
    {post !== null ? <Segment from={scale.x(entry)} to={scale.x(entry + post)} color={post >= 0 ? PATH_COLORS.ourWay : PATH_COLORS.againstUs} /> : null}
    <span className={`absolute top-2.5 -ml-1 size-2 rounded-full ring-2 ring-base-200 ${marker}`} style={{ left: `${scale.x(entry)}%` }} />
  </span>
}

function tickPlacement(tick: number, scale: Scale): string {
  if (tick === scale.min) return ''
  if (tick === scale.max) return '-translate-x-full'
  return '-translate-x-1/2'
}

function MoveAxis({ scale }: { scale: Scale }) {
  return <span className="relative mt-1.5 block h-3.5 w-36 sm:w-60" aria-hidden="true">
    {[scale.min, 0, scale.max].map((tick) => <span key={tick} className={`absolute top-0 whitespace-nowrap font-mono text-[11px] text-dim ${tickPlacement(tick, scale)}`} style={{ left: `${scale.x(tick)}%` }}>
      {tick === 0 ? '0' : `${tick > 0 ? '+' : '−'}${Math.abs(tick)}%`}
    </span>)}
  </span>
}

function Swatch({ color, children }: { color: string; children: string }) {
  return <span className="flex items-center gap-1.5 text-[11px] text-dim"><i className="h-2 w-3.5 rounded-sm" style={{ background: color }} />{children}</span>
}

// ---- Headline charts --------------------------------------------------------

function MoveShare({ share }: { share: number | null }) {
  if (share === null) return <p className="text-dim">No entry has both a publish and an exit price yet.</p>
  const before = Math.round(share * 100)
  return <div className="flex flex-col gap-2">
    <div role="img" aria-label={`${before}% of the move came before entry, ${100 - before}% after`} className="flex h-5 overflow-hidden rounded-sm text-[11px] font-medium">
      <div className="flex items-center px-1.5 text-base-content" style={{ width: `${before}%`, background: PATH_COLORS.before }}>{before}%</div>
      <div className="flex flex-1 items-center justify-end px-1.5 text-info-content" style={{ background: PATH_COLORS.ourWay }}>{100 - before}%</div>
    </div>
    <p className="text-[11px] text-dim">Median share of the stock's publish-to-exit move that came before / after our entry</p>
  </div>
}

function RightWayLost({ lost, closed }: { lost: number; closed: number }) {
  if (!closed) return <p className="text-dim">No closed trade has gone our way yet.</p>
  return <div className="flex flex-col gap-2">
    <div role="img" aria-label={`${lost} of ${closed} closed trades went our way after entry but the option lost`} className="grid grid-cols-[repeat(auto-fill,minmax(14px,1fr))] gap-1">
      {Array.from({ length: closed }, (_, index) => <span key={index} className={`h-3.5 rounded-sm ${index < lost ? 'bg-primary' : 'bg-base-300'}`} />)}
    </div>
    <p className="text-[11px] text-dim">{lost} of {closed} closed trades: the stock went our way after entry and the option still closed red. That points at contract choice and timing, not direction.</p>
  </div>
}

// ---- Page -------------------------------------------------------------------

const WINDOW_OPTIONS = [
  { value: '0', label: 'All history' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '180', label: '180 days' },
  { value: '365', label: '365 days' },
]

const BUCKET_COLUMNS: Column<[Bucket, BucketSummary]>[] = [
  { key: 'lag', header: 'Lag', render: ([bucket, summary]) => <>
    <span className="font-medium">{BUCKET_LABELS[bucket]}</span>
    {summary.closed < SAMPLE_GUARDRAIL && summary.trades ? <span className="block text-[11px] text-dim">below sample guardrail</span> : null}
  </> },
  { key: 'entries', header: 'Entries', align: 'right', render: ([, summary]) => <>{summary.trades}<span className="block text-[11px] text-dim">{summary.closed} closed</span></> },
  { key: 'win', header: 'Win rate', align: 'right', render: ([, summary]) => summary.option_pnl_pct.win_rate === null ? EMPTY : `${Math.round(summary.option_pnl_pct.win_rate * 100)}%` },
  { key: 'mean', header: 'Mean P/L', align: 'right', render: ([, summary]) => <span className={pnlClass(summary.option_pnl_pct.mean)}>{formatMove(summary.option_pnl_pct.mean)}</span> },
  { key: 'median', header: 'Median P/L', align: 'right', render: ([, summary]) => formatMove(summary.option_pnl_pct.median) },
  { key: 'before', header: 'Median before', align: 'right', render: ([, summary]) => formatMove(summary.pre_entry_move_pct.median) },
  { key: 'after', header: 'Median after', align: 'right', render: ([, summary]) => formatMove(summary.post_entry_move_pct.median) },
  { key: 'share', header: 'Share before', align: 'right', render: ([, summary]) => formatShare(summary.pre_entry_share.median) },
]

function entryColumns(scale: Scale): Column<Trade>[] {
  return [
    { key: 'entry', header: 'Entry', render: (trade) => <>
      <TickerChip symbol={trade.symbol} />
      <span className="block whitespace-nowrap text-[11px] text-dim">{formatDate(trade.ordered_at ?? '', { placeholder: EMPTY })} · {trade.direction ?? EMPTY}</span>
    </> },
    { key: 'lag', header: 'Lag', render: (trade) => <>{formatLag(trade.lag_hours)}<span className="block whitespace-nowrap text-[11px] text-dim">{BUCKET_LABELS[trade.lag_bucket]}</span></> },
    { key: 'path', header: <>Stock move, in trade direction<MoveAxis scale={scale} /></>, render: (trade) => <MoveSplit trade={trade} scale={scale} /> },
    { key: 'before', header: 'Before', align: 'right', render: (trade) => formatMove(trade.pre_entry_move_pct) },
    { key: 'after', header: 'After', align: 'right', render: (trade) => formatMove(trade.post_entry_move_pct) },
    { key: 'pnl', header: 'Option P/L', align: 'right', render: (trade) => <>
      <span className={pnlClass(trade.option_pnl_pct)}>{trade.closed ? formatMove(trade.option_pnl_pct) : 'Open'}</span>
      {trade.favourable_but_lost ? <span className="block text-[11px] text-warning">Moved our way, option lost</span> : null}
    </> },
    { key: 'exit', header: 'Exit', render: (trade) => formatToken(trade.exit_reason, { placeholder: EMPTY }) },
  ]
}

function NewsLagPage() {
  const [days, setDays] = useState(0)
  // A new loader per window re-reads the report; the hook keeps the last payload until it arrives.
  const load = useMemo(() => () => getNewsLag(days), [days])
  const { payload, error, isLoading } = useFetchedResource(load, { errorMessage: 'Failed to load the news-lag report.' })

  const dated = useMemo(() => [...(payload?.trades ?? [])]
    .filter((trade) => trade.lag_hours !== null)
    .sort((left, right) => (left.lag_hours ?? 0) - (right.lag_hours ?? 0)), [payload])
  const scale = useMemo(() => makeScale(dated), [dated])
  const dailySampled = payload?.all.daily_sampled_publish_prices ?? 0

  return <main className="flex flex-col gap-3">
    <PageHeader
      title="How late we trade the news"
      description="Time from the newest article the strategist cited to our order, and how far the stock moved before and after we entered. Read-only."
      meta={payload ? `As of ${formatDate(payload.as_of, { placeholder: 'Unknown' })}` : null}
      actions={<Select label="Window" options={WINDOW_OPTIONS} value={String(days)} onChange={(value) => setDays(Number(value))} />}
    />

    {error ? <p role="alert" className="text-xs text-error">{error}</p> : null}
    {isLoading && !payload ? <p className="text-dim">Loading the news-lag report…</p> : null}
    {payload ? <>
      <StatStrip items={[
        { label: 'Median news → order lag', value: formatLag(payload.headline.median_lag_hours), sub: `${payload.headline.within_1h} of ${payload.headline.dated_entries} entries within 1 h` },
        { label: 'Move gone before entry', value: formatShare(payload.headline.median_pre_entry_share), sub: 'median share, publish → exit' },
        { label: 'Right way, option lost', value: `${payload.headline.favourable_but_lost} of ${payload.headline.favourable_closed}`, sub: 'closed trades' },
        { label: 'Measurable entries', value: `${payload.headline.measurable} of ${payload.headline.entries}`, sub: 'dated citation + prices' },
        { label: 'Fastest entry', value: formatLag(dated[0]?.lag_hours ?? null), sub: dated[0] ? `${dated[0].symbol}, ${formatDate(dated[0].ordered_at ?? '', { placeholder: EMPTY })}` : undefined },
      ]} />

      {dailySampled > 0 ? <p className="text-xs text-warning">{dailySampled} publish {dailySampled === 1 ? 'price falls' : 'prices fall'} back to the prior daily close, so their before-entry move includes overnight drift · fix: <code className="font-mono">research.py news-lag --fetch-missing</code></p> : null}

      <div className="flex flex-wrap gap-3">
        <Panel className="min-w-72 flex-[2_1_28rem]" title="Lag vs. option P/L" meta="each dot = one closed entry">
          <LagScatter trades={payload.trades} medianLagHours={payload.headline.median_lag_hours} />
          <p className="mt-2 text-[11px] text-dim">Shaded: the first hour after the news · dashed: the median lag</p>
        </Panel>
        <div className="flex min-w-64 flex-[1_1_18rem] flex-col gap-3">
          <Panel title="Where the move happened"><MoveShare share={payload.headline.median_pre_entry_share} /></Panel>
          <Panel title="Right direction, option still lost"><RightWayLost lost={payload.headline.favourable_but_lost} closed={payload.headline.favourable_closed} /></Panel>
        </div>
      </div>

      <Panel
        title="Every dated entry"
        meta="publish → entry → exit, ordered by lag"
        actions={<>
          <Swatch color={PATH_COLORS.before}>Before entry</Swatch>
          <Swatch color={PATH_COLORS.ourWay}>After, our way</Swatch>
          <Swatch color={PATH_COLORS.againstUs}>After, against us</Swatch>
        </>}
        flush
      >
        <DataTable label="Every dated entry" columns={entryColumns(scale)} rows={dated} rowKey={(trade) => trade.order_id} emptyMessage="No entries with a dated citation in this window." />
      </Panel>

      <Panel title="Option P/L by how old the news was" meta={`buckets with fewer than ${SAMPLE_GUARDRAIL} closed trades are muted`} flush>
        <DataTable
          label="Lag buckets"
          columns={BUCKET_COLUMNS}
          rows={(Object.keys(BUCKET_LABELS) as Bucket[]).map((bucket) => [bucket, payload.buckets[bucket]] as [Bucket, BucketSummary])}
          rowKey={([bucket]) => bucket}
          rowClassName={([, summary]) => (summary.closed < SAMPLE_GUARDRAIL ? 'opacity-60' : '')}
        />
      </Panel>

      <p className="text-[11px] text-dim">
        {payload.coverage.entries} filled entries in this window
        {Object.entries(payload.coverage.missing).map(([reason, count]) => ` · ${count} ${MISSING_LABELS[reason] ?? reason.replaceAll('_', ' ')}`).join('')}
        {payload.coverage.future_dated_citations ? ` · ${payload.coverage.future_dated_citations} cited ${payload.coverage.future_dated_citations === 1 ? 'article is' : 'articles are'} dated after the order (likely re-scraped) and ignored for lag` : ''}
      </p>
    </> : null}
  </main>
}

export default NewsLagPage
