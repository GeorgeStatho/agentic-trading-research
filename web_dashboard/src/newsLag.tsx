import { useMemo, useState } from 'react'

import { formatDate, formatSignedPercent, formatToken } from './format'
import PageHeader from './pageHeader'
import { useFetchedResource } from './useRemoteResource'
import './decisionExpectancy.css'
import './newsLag.css'

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
  if (value === null) return ''
  return value < 0 ? 'decision-expectancy__negative' : 'decision-expectancy__positive'
}

async function getNewsLag(days: number): Promise<Payload> {
  const response = await fetch(`/api/news-lag?days=${days}`)
  const body = await response.json().catch(() => null) as Payload | { error?: string } | null
  if (!response.ok) throw new Error((body as { error?: string } | null)?.error ?? `Failed to load the news-lag report: ${response.status}`)
  return body as Payload
}

// ---- Move-split bar ---------------------------------------------------------
// Plain HTML positioned in percent, so the bar can narrow on phones without shrinking its axis text.

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
function Segment({ from, to, className }: { from: number; to: number; className: string }) {
  if (Math.abs(to - from) < 0.2) return null
  return <span
    className={`news-lag__segment ${className} ${to >= from ? 'news-lag__segment--right' : 'news-lag__segment--left'}`}
    style={{ left: `${Math.min(from, to)}%`, width: `${Math.abs(to - from)}%` }}
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
  return <span className="news-lag__bar" role="img" aria-label={summary} title={summary}>
    <span className="news-lag__zero" style={{ left: `${scale.x(0)}%` }} />
    {pre !== null ? <Segment from={scale.x(0)} to={scale.x(pre)} className="news-lag__before" /> : null}
    {post !== null ? <Segment from={scale.x(entry)} to={scale.x(entry + post)} className={post >= 0 ? 'news-lag__after-good' : 'news-lag__after-bad'} /> : null}
    <span className={pre === null ? 'news-lag__entry news-lag__entry--unknown' : 'news-lag__entry'} style={{ left: `${scale.x(entry)}%` }} />
  </span>
}

function MoveAxis({ scale }: { scale: Scale }) {
  return <span className="news-lag__axis" aria-hidden="true">
    {[scale.min, 0, scale.max].map((tick) => <span key={tick} className={tick === scale.min ? 'news-lag__tick news-lag__tick--start' : tick === scale.max ? 'news-lag__tick news-lag__tick--end' : 'news-lag__tick'} style={{ left: `${scale.x(tick)}%` }}>
      {tick === 0 ? '0' : `${tick > 0 ? '+' : '−'}${Math.abs(tick)}%`}
    </span>)}
  </span>
}

// ---- Page -------------------------------------------------------------------

function NewsLagPage() {
  const [days, setDays] = useState(0)
  // A new loader per window re-reads the report; the hook keeps the last payload until it arrives.
  const load = useMemo(() => () => getNewsLag(days), [days])
  const { payload, error, isLoading: loading } = useFetchedResource(load, { errorMessage: 'Failed to load the news-lag report.' })

  const dated = useMemo(() => [...(payload?.trades ?? [])]
    .filter((trade) => trade.lag_hours !== null)
    .sort((left, right) => (left.lag_hours ?? 0) - (right.lag_hours ?? 0)), [payload])
  const scale = useMemo(() => makeScale(dated), [dated])
  const headline = payload?.headline
  const dailySampled = payload?.all.daily_sampled_publish_prices ?? 0

  return <main className="decision-expectancy news-lag">
    <PageHeader
      title="How late we trade the news"
      description="Time from the newest article the strategist cited to our order, and how far the stock moved before and after we entered. Read-only."
      meta={payload ? `As of ${formatDate(payload.as_of, { placeholder: 'Unknown' })}` : null}
    />

    <section className="decision-expectancy__controls" aria-label="News lag filters">
      <label>Window<select value={days} onChange={(event) => setDays(Number(event.target.value))}>
        <option value={30}>30 days</option><option value={90}>90 days</option><option value={180}>180 days</option><option value={365}>365 days</option><option value={0}>All history</option>
      </select></label>
    </section>

    {error ? <section className="decision-expectancy__empty" role="alert">{error}</section> : null}
    {loading && !payload ? <section className="decision-expectancy__empty">Loading the news-lag report…</section> : null}
    {payload && headline ? <>
      <section className="decision-expectancy__summary news-lag__summary" aria-label="News lag headline">
        <article><span>Median news-to-order lag</span><b>{formatLag(headline.median_lag_hours)}</b><small>{headline.within_1h} of {headline.dated_entries} entries within 1 hour</small></article>
        <article><span>Move that happened before entry</span><b>{formatShare(headline.median_pre_entry_share)}</b><small>Median share of the stock's move between publish and exit</small></article>
        <article><span>Stock moved our way, option lost</span><b>{headline.favourable_but_lost} of {headline.favourable_closed}</b><small>Of closed trades where the stock went our way after entry, the option still closed red</small></article>
        <article><span>Measurable entries</span><b>{headline.measurable} of {headline.entries}</b><small>The rest lack a dated citation, a decision link or prices</small></article>
      </section>

      {dailySampled > 0 ? <section className="decision-expectancy__notice">{dailySampled} publish {dailySampled === 1 ? 'price falls' : 'prices fall'} back to the prior daily close, so their before-entry move includes overnight drift. Run <code>research.py news-lag --fetch-missing</code> on the server to cache hourly bars.</section> : null}

      <section className="decision-expectancy__table-card">
        <header>
          <div><p>Every dated entry</p><h2>Stock path from publish to entry to exit, ordered by lag</h2></div>
          <ul className="news-lag__legend" aria-label="Bar legend">
            <li><i className="news-lag__swatch news-lag__swatch--before" />Before entry</li>
            <li><i className="news-lag__swatch news-lag__swatch--good" />After entry, our way</li>
            <li><i className="news-lag__swatch news-lag__swatch--bad" />After entry, against us</li>
          </ul>
        </header>
        {dated.length ? <div className="decision-expectancy__table-wrap"><table className="news-lag__trades"><thead><tr>
          <th>Entry</th><th>Lag</th><th className="news-lag__bar-head">Stock move, in trade direction<MoveAxis scale={scale} /></th><th>Before</th><th>After</th><th>Option P&amp;L</th><th>Exit</th>
        </tr></thead><tbody>{dated.map((trade) => <tr key={trade.order_id} className={trade.favourable_but_lost ? 'news-lag__flagged' : ''}>
          <td><strong>{trade.symbol}</strong><small>{formatDate(trade.ordered_at ?? '', { placeholder: EMPTY })} · {trade.direction ?? EMPTY}</small><small className="news-lag__mobile-only">{formatLag(trade.lag_hours)} after the news</small></td>
          <td>{formatLag(trade.lag_hours)}<small>{BUCKET_LABELS[trade.lag_bucket]}</small></td>
          <td><MoveSplit trade={trade} scale={scale} /></td>
          <td>{formatMove(trade.pre_entry_move_pct)}</td>
          <td>{formatMove(trade.post_entry_move_pct)}</td>
          <td><span className={pnlClass(trade.option_pnl_pct)}>{trade.closed ? formatMove(trade.option_pnl_pct) : 'Open'}</span>{trade.favourable_but_lost ? <small className="news-lag__flag">Moved our way, option lost</small> : null}</td>
          <td>{formatToken(trade.exit_reason, { placeholder: EMPTY })}</td>
        </tr>)}</tbody></table></div> : <p className="decision-expectancy__empty news-lag__inset">No entries with a dated citation in this window.</p>}
      </section>

      <section className="decision-expectancy__table-card">
        <header><div><p>Lag buckets</p><h2>Option P&amp;L by how old the news was</h2></div><span>Buckets with fewer than {SAMPLE_GUARDRAIL} closed trades are muted</span></header>
        <div className="decision-expectancy__table-wrap"><table><thead><tr>
          <th>Lag</th><th>Entries</th><th>Win rate</th><th>Mean P&amp;L</th><th>Median P&amp;L</th><th>Median before</th><th>Median after</th><th>Share before</th>
        </tr></thead><tbody>{(Object.keys(BUCKET_LABELS) as Bucket[]).map((bucket) => {
          const summary = payload.buckets[bucket]
          const muted = summary.closed < SAMPLE_GUARDRAIL
          const pnl = summary.option_pnl_pct
          return <tr key={bucket} className={muted ? 'decision-expectancy__muted' : ''}>
            <td><strong>{BUCKET_LABELS[bucket]}</strong>{muted && summary.trades ? <small>Below sample guardrail</small> : null}</td>
            <td>{summary.trades}<small>{summary.closed} closed</small></td>
            <td>{pnl.win_rate === null ? EMPTY : `${Math.round(pnl.win_rate * 100)}%`}</td>
            <td className={pnlClass(pnl.mean)}>{formatMove(pnl.mean)}</td>
            <td>{formatMove(pnl.median)}</td>
            <td>{formatMove(summary.pre_entry_move_pct.median)}</td>
            <td>{formatMove(summary.post_entry_move_pct.median)}</td>
            <td>{formatShare(summary.pre_entry_share.median)}</td>
          </tr>
        })}</tbody></table></div>
      </section>

      <section className="news-lag__coverage" aria-label="Coverage">
        <p>{payload.coverage.entries} filled entries in this window.</p>
        {Object.keys(payload.coverage.missing).length ? <ul>{Object.entries(payload.coverage.missing).map(([reason, count]) => <li key={reason}>{count} · {MISSING_LABELS[reason] ?? reason.replaceAll('_', ' ')}</li>)}</ul> : null}
        {payload.coverage.future_dated_citations ? <p>{payload.coverage.future_dated_citations} cited {payload.coverage.future_dated_citations === 1 ? 'article is' : 'articles are'} dated after the order (likely re-scraped) and ignored for lag.</p> : null}
      </section>
    </> : null}
  </main>
}

export default NewsLagPage
