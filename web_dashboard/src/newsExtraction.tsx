import { useMemo, useState } from 'react'

import PageHeader from './pageHeader'
import { useFetchedResource } from './useRemoteResource'
import './decisionExpectancy.css'
import './newsExtraction.css'

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
  return <span className="news-extraction__bar" role="img" aria-label={summary} title={summary}>
    {row.success_rate > 0 ? <span className="news-extraction__fill" style={{ width: `${row.success_rate * 100}%` }} /> : null}
    <span className="news-extraction__pass" style={{ left: `${PASS_RATE * 100}%` }} />
  </span>
}

function NewsExtractionPage() {
  const [days, setDays] = useState(7)
  const load = useMemo(() => () => getNewsExtraction(days), [days])
  const { payload, error, isLoading: loading } = useFetchedResource(load, { errorMessage: 'Failed to load the extraction report.' })

  const totals = useMemo(() => {
    const rows = payload?.sources ?? []
    const attempted = rows.reduce((sum, row) => sum + row.attempted, 0)
    const succeeded = rows.reduce((sum, row) => sum + row.succeeded, 0)
    return {
      attempted,
      succeeded,
      rate: attempted ? succeeded / attempted : null,
      permanent: rows.reduce((sum, row) => sum + row.permanent_failures, 0),
      failing: rows.filter((row) => row.attempted >= SAMPLE_GUARDRAIL && row.success_rate < PASS_RATE).length,
      measured: rows.filter((row) => row.attempted >= SAMPLE_GUARDRAIL).length,
    }
  }, [payload])

  return <main className="decision-expectancy news-extraction">
    <PageHeader
      title="Article extraction by source"
      description="How often a followed article link was saved rather than failing extraction, per source. Read-only."
      meta={payload ? `${payload.start} to ${payload.end} (UTC)` : null}
    />

    <section className="decision-expectancy__controls" aria-label="Extraction filters">
      <label>Window<select value={days} onChange={(event) => setDays(Number(event.target.value))}>
        <option value={1}>Today</option><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option>
      </select></label>
    </section>

    {error ? <section className="decision-expectancy__empty" role="alert">{error}</section> : null}
    {loading && !payload ? <section className="decision-expectancy__empty">Loading the extraction report…</section> : null}
    {payload ? <>
      <section className="decision-expectancy__summary news-extraction__summary" aria-label="Extraction headline">
        <article><span>Overall success rate</span><b>{formatRate(totals.rate)}</b><small>{totals.succeeded} of {totals.attempted} urls saved</small></article>
        <article><span>Sources below {formatRate(PASS_RATE)}</span><b>{totals.failing} of {totals.measured}</b><small>Of sources with at least {SAMPLE_GUARDRAIL} attempted urls</small></article>
        <article><span>Blocked urls</span><b>{totals.permanent}</b><small>Failed with HTTP 403 or 404 and not retried</small></article>
        <article><span>Sources seen</span><b>{payload.sources.length}</b><small>With a saved or failed url in this window</small></article>
      </section>

      <section className="decision-expectancy__table-card">
        <header>
          <div><p>Per source</p><h2>Share of attempted urls that were extracted and saved</h2></div>
          <span>The tick marks the {formatRate(PASS_RATE)} pass mark; sources with fewer than {SAMPLE_GUARDRAIL} urls are muted</span>
        </header>
        {payload.sources.length ? <div className="decision-expectancy__table-wrap"><table className="news-extraction__table"><thead><tr>
          <th>Source</th><th>Success rate</th><th className="news-extraction__bar-head">0–100%</th><th>Saved</th><th>Failed</th><th>Blocked</th><th>Most common error</th>
        </tr></thead><tbody>{payload.sources.map((row) => {
          const muted = row.attempted < SAMPLE_GUARDRAIL
          const below = !muted && row.success_rate < PASS_RATE
          return <tr key={row.source} className={muted ? 'decision-expectancy__muted' : ''}>
            <td><strong>{row.source}</strong>{below ? <small className="news-extraction__flag">▲ Below pass mark</small> : muted ? <small>Below sample guardrail</small> : null}</td>
            <td>{formatRate(row.success_rate)}<small>of {row.attempted}</small></td>
            <td><RateBar row={row} /></td>
            <td>{row.succeeded}</td>
            <td>{row.failed}</td>
            <td>{row.permanent_failures}</td>
            <td className="news-extraction__error">{row.top_error ?? EMPTY}</td>
          </tr>
        })}</tbody></table></div> : <p className="decision-expectancy__empty news-extraction__inset">No article links were followed in this window.</p>}
      </section>

      <p className="news-extraction__note">Each url counts once. A url that failed and later succeeded counts as saved; failures are dated by their latest attempt. The same numbers come from <code>scripts/news_quality_report.py</code>.</p>
    </> : null}
  </main>
}

export default NewsExtractionPage
