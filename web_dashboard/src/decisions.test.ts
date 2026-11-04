import { describe, expect, it } from 'vitest'

import {
  articleAgeHours,
  decisionCounts,
  decisionPath,
  diagnosticNotices,
  filterDecisions,
  funnelCounts,
  safeguardVerdicts,
  stoppedAt,
  whyLine,
} from './decisions.ts'
import { locationFromHash, searchFromHash } from './routes.tsx'
import { blockedCall, decisionRecord } from './test/decisionFixture.ts'

function steps(record = decisionRecord()) {
  return decisionPath(record).map((step) => `${step.name}: ${step.state} · ${step.detail}`)
}

describe('decisionPath', () => {
  it('stops at the strategist when it does not name a trade candidate, and the manager is never asked', () => {
    expect(steps()).toEqual([
      'News: passed · 1 article cited',
      'Strategist: stopped · Watchlist',
      'Manager: not_reached · Not asked',
      'Safeguards: also · Liquidity V2 (E2) +1 more',
      'Order: not_reached · No contract chosen',
    ])
  })

  it('stops at the manager when it declines a candidate', () => {
    const record = decisionRecord({
      strategist: { decision: 'trade_candidate' },
      manager: { decision: 'neither', reason: 'Wait for confirmation.' },
      v2_verdicts: [],
    })
    expect(steps(record).slice(1)).toEqual([
      'Strategist: passed · Trade candidate',
      'Manager: stopped · No trade',
      'Safeguards: not_reached · Not reached',
      'Order: not_reached · No contract chosen',
    ])
  })

  it('stops at the safeguards when an enforced verdict blocks a call, and counts the shadow ones', () => {
    expect(steps(blockedCall()).slice(2)).toEqual([
      'Manager: passed · Call',
      'Safeguards: stopped · Premium cap hit',
      'Order: not_reached · Not submitted',
    ])
  })

  it('passes every step when the order went out', () => {
    const record = blockedCall({
      v2_verdicts: [{ stage: 'REGIME', label: 'Market regime scope (G3)', reason_code: 'regime_range', shadow: true, occurred_at: '' }],
      outcome: { trade_executed: true, order_id: 'abc' },
    })
    expect(steps(record).slice(3)).toEqual([
      'Safeguards: passed · 1 would have blocked',
      'Order: passed · MSFT 18 Dec $560C',
    ])
  })

  it('stops at the order when the safeguards passed but nothing was filled', () => {
    expect(steps(blockedCall({ v2_verdicts: [] })).slice(3)).toEqual([
      'Safeguards: passed · All passed',
      'Order: stopped · Not filled',
    ])
  })

  it('says when no article was cited', () => {
    expect(steps(decisionRecord({ evidence: [] }))[0]).toBe('News: passed · No articles cited')
  })
})

describe('stoppedAt', () => {
  it('names the stage that stopped the decision, or nothing once it traded', () => {
    expect(stoppedAt(decisionRecord())).toBe('strategist')
    expect(stoppedAt(decisionRecord({ strategist: { decision: 'trade_candidate' } }))).toBe('manager')
    expect(stoppedAt(blockedCall())).toBe('safeguards')
    expect(stoppedAt(blockedCall({ v2_verdicts: [] }))).toBe('order')
    expect(stoppedAt(blockedCall({ v2_verdicts: [], outcome: { trade_executed: true } }))).toBeNull()
  })
})

describe('safeguardVerdicts', () => {
  it('drops the rejections that only follow from the manager saying no', () => {
    expect(safeguardVerdicts(decisionRecord()).map((verdict) => verdict.label)).toEqual([
      'Liquidity V2 (E2)',
      'Provenance roots (P1)',
    ])
  })

  it('is empty when the record carries no verdicts', () => {
    expect(safeguardVerdicts(decisionRecord({ v2_verdicts: undefined }))).toEqual([])
  })
})

describe('whyLine', () => {
  it('says what the strategist chose when it stopped there', () => {
    expect(whyLine(decisionRecord())).toBe('Strategist chose watchlist · medium confidence')
  })

  it('names the blocking safeguard', () => {
    expect(whyLine(blockedCall())).toBe('Blocked by Execution: Premium cap hit')
  })

  it("otherwise gives the manager's reason", () => {
    const record = decisionRecord({ strategist: { decision: 'trade_candidate' }, manager: { reason: 'Wait for confirmation.' } })
    expect(whyLine(record)).toBe('Wait for confirmation.')
  })
})

describe('decisionCounts', () => {
  it('counts each funnel stage: evaluated, trade candidates, calls, puts and executed orders', () => {
    const records = [
      decisionRecord(),
      blockedCall(),
      blockedCall({ id: 1, manager: { decision: 'put' }, outcome: { trade_executed: true } }),
    ]
    expect(decisionCounts(records)).toEqual({ evaluated: 3, tradeCandidates: 2, call: 1, put: 1, executed: 1 })
  })

  it("reads the same stages from the server's count of the whole day", () => {
    expect(funnelCounts({ market_day: '2026-11-04', evaluated: 122, trade_candidates: 13, call: 4, put: 1, executed: 1 }))
      .toEqual({ evaluated: 122, tradeCandidates: 13, call: 4, put: 1, executed: 1 })
  })
})

describe('filterDecisions', () => {
  const now = Date.parse('2026-11-04T20:00:00Z')
  const yesterday = decisionRecord({ id: 1, symbol: 'PBF', company_name: 'PBF Energy', decision_run_at: '2026-11-03T20:38:00+00:00' })
  const records = [decisionRecord(), blockedCall(), yesterday]
  const all = { query: '', verdict: 'all', stoppedAt: 'any', when: 'all' } as const

  function ids(filters: Partial<Parameters<typeof filterDecisions>[1]>) {
    return filterDecisions(records, { ...all, ...filters }, now).map((record) => record.symbol)
  }

  it('keeps everything with no filter set', () => {
    expect(ids({})).toEqual(['DVN', 'MSFT', 'PBF'])
  })

  it('matches the search against ticker and company, ignoring case', () => {
    expect(ids({ query: 'msf' })).toEqual(['MSFT'])
    expect(ids({ query: 'pbf energy' })).toEqual(['PBF'])
  })

  it("filters by the manager's verdict", () => {
    expect(ids({ verdict: 'call' })).toEqual(['MSFT'])
    expect(ids({ verdict: 'neither' })).toEqual(['DVN', 'PBF'])
    expect(ids({ verdict: 'put' })).toEqual([])
  })

  it('filters by the stage that stopped the decision', () => {
    expect(ids({ stoppedAt: 'safeguards' })).toEqual(['MSFT'])
    expect(ids({ stoppedAt: 'strategist' })).toEqual(['DVN', 'PBF'])
  })

  it('keeps only decisions from the current trading day (ET) for "today"', () => {
    expect(ids({ when: 'today' })).toEqual(['DVN', 'MSFT'])
  })
})

describe('articleAgeHours', () => {
  it('is how old an article was when the decision ran', () => {
    expect(articleAgeHours('2026-11-03T18:09:15Z', '2026-11-04T16:27:24+00:00')).toBeCloseTo(22.3, 1)
  })

  it('is null when either time is missing', () => {
    expect(articleAgeHours('', '2026-11-04T16:27:24+00:00')).toBeNull()
  })
})

describe('Decisions routes', () => {
  it('sends the old Ledger and Autopsy addresses to the merged view', () => {
    for (const hash of ['#/decisions/ledger', '#/decisions/autopsy', '#/company-decisions', '#/decision-autopsy', '#/decisions']) {
      expect(locationFromHash(hash).tab.slug).toBe('decisions')
    }
    expect(locationFromHash('#/decisions/news').tab.slug).toBe('news')
  })

  it('reads the search a link carries', () => {
    expect(searchFromHash('#/decisions/ledger?q=amd')).toBe('amd')
    expect(searchFromHash('#/decisions/news?demo=1&q=Apple%20Inc')).toBe('Apple Inc')
    expect(searchFromHash('#/decisions')).toBe('')
  })
})

describe('diagnosticNotices', () => {
  it("lists each company whose latest strategist attempt failed or whose price history could not load", () => {
    const feed = {
      last_updated_at: '',
      companies: [
        { symbol: 'AAPL', name: 'Apple', last_updated_at: '', strategist: null, manager: { decision: '' },
          diagnostics: { strategist_validation: { status: 'failed', error_message: 'Reply was not valid JSON', input_article_count: 4, created_at: '' } } },
        { symbol: 'XOM', name: 'Exxon', last_updated_at: '', strategist: null, manager: { decision: '' },
          diagnostics: { historical_bars: { error: 'No bars returned', feed: 'iex' } } },
        { symbol: 'DVN', name: 'Devon', last_updated_at: '', strategist: null, manager: { decision: '' } },
      ],
    }
    expect(diagnosticNotices(feed)).toEqual([
      'AAPL: latest strategist attempt failed (4 articles supplied): Reply was not valid JSON',
      'XOM: price history unavailable: No bars returned',
    ])
  })
})
