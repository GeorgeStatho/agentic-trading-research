import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { HoldingsView } from './openPositionsTable.tsx'
import type { OpenPosition } from './openPositionsTable.tsx'
import { LivePipelineView } from './livePipeline.tsx'
import { pipelineTotals } from './pipelineTotals.ts'
import { LiveSafeguardsView } from './liveSafeguards.tsx'
import { kpiPayload } from './test/kpiFixture.ts'
import { safeguardNotices } from './v2StatusApi.ts'
import type { V2StatusPayload } from './v2StatusApi.ts'

function v2(overrides: Partial<V2StatusPayload> = {}): V2StatusPayload {
  return {
    as_of: '2026-11-04T07:58:34Z',
    window_days: 30,
    source_hash: null,
    mode_summary: { enforcing: 0, shadow: 8, disabled: 0, downgraded: 3, misconfigured: 0 },
    activity_available: true,
    features: Array.from({ length: 8 }, () => ({}) as V2StatusPayload['features'][number]),
    shadow_only_features: [],
    research: {} as V2StatusPayload['research'],
    prerequisites: [],
    ...overrides,
  }
}

describe('safeguardNotices', () => {
  it('explains downgraded controls, in the singular when there is one', () => {
    expect(safeguardNotices(v2())).toEqual([
      '3 controls are set to enforce but running in shadow: no promotion proof matches the current thresholds and source.',
    ])
    expect(safeguardNotices(v2({ mode_summary: { enforcing: 0, shadow: 8, disabled: 0, downgraded: 1, misconfigured: 0 } }))[0])
      .toMatch(/^1 control is set to enforce/)
  })

  it('warns about unreadable controls and missing activity tables', () => {
    const notices = safeguardNotices(v2({
      mode_summary: { enforcing: 0, shadow: 8, disabled: 0, downgraded: 0, misconfigured: 2 },
      activity_available: false,
    }))
    expect(notices).toHaveLength(2)
    expect(notices[0]).toMatch(/^2 controls could not be read/)
    expect(notices[1]).toMatch(/^No candidate lifecycle tables/)
  })
})

describe('Safeguards panel', () => {
  it('shows what is enforcing, what was downgraded and the thesis flip rate, with the warning as one line', () => {
    render(<LiveSafeguardsView v2={v2()} validator={kpiPayload().thesis_validator} />)

    const panel = screen.getByRole('region', { name: /Safeguards/ })
    expect(panel.textContent).toContain('V2 enforcing0 / 8')
    expect(panel.textContent).toContain('Downgraded3')
    expect(panel.textContent).toContain('Thesis flip12%')
    expect(within(panel).getByText(/3 controls are set to enforce/).className).toContain('text-warning')
    expect(within(panel).getByRole('link', { name: 'See the evidence' }).getAttribute('href')).toBe('#/research/rollout')
  })
})

describe('Data pipeline panel', () => {
  const extraction = {
    days: 7,
    sources: [
      { source: 'finance.yahoo.com', attempted: 2673, succeeded: 2673 },
      { source: 'benzinga.com', attempted: 300, succeeded: 263 },
    ],
  }

  it('totals extraction across sources', () => {
    expect(pipelineTotals(extraction)).toEqual({ successRate: 2936 / 2973, saved: 2936, sources: 2 })
  })

  it('shows the success rate, URLs saved and source count for the window', () => {
    render(<LivePipelineView extraction={extraction} />)

    const panel = screen.getByRole('region', { name: /Data pipeline/ })
    expect(panel.textContent).toContain('7 days')
    expect(panel.textContent).toContain('Extraction99%')
    expect(panel.textContent).toContain('URLs saved2,936')
    expect(panel.textContent).toContain('Sources2')
    expect(within(panel).getByRole('link', { name: 'Open' }).getAttribute('href')).toBe('#/system/extraction')
  })
})

describe('Holdings panel', () => {
  const position = {
    symbol: 'ORCL',
    contract_symbol: 'ORCL261120P00121000',
    position_kind: 'option',
    type: 'put',
    strike: 121,
    expiration: '2026-11-20',
    quantity: 1,
    entry_price: 2.1,
    current_bid: 2.2,
    current_ask: 2.4,
    mark_price: 2.3,
    mid_price: 2.3,
    unrealized_pl_pct: 9.52,
    days_to_expiration: 16,
    profit_protection: null,
    exit_rule_status: 'holding',
    decision_reasons: [],
    thesis_verdict: null,
  } as OpenPosition

  it('says so plainly when nothing is open', () => {
    render(<HoldingsView positions={[]} />)
    expect(within(screen.getByRole('region', { name: /Holdings/ })).getByText('No open positions.')).toBeTruthy()
  })

  it('lists each position with its contract, size, prices, P/L and days left', () => {
    render(<HoldingsView positions={[position]} />)

    const row = screen.getByText('ORCL 20 Nov $121P').closest('tr') as HTMLElement
    expect(row.textContent).toContain('$2.10')
    expect(row.textContent).toContain('$2.30')
    expect(within(row).getByText('+9.52%').className).toContain('text-gain')
    expect(row.textContent).toContain('16')
  })
})
