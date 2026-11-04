import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { DecisionsPayload } from './decisions.ts'
import { DecisionsView } from './decisionsPage.tsx'
import { blockedCall, decisionRecord } from './test/decisionFixture.ts'

const yesterday = decisionRecord({ id: 1, symbol: 'PBF', company_name: 'PBF Energy', decision_run_at: '2026-11-03T20:38:00+00:00' })

function payload(overrides: Partial<DecisionsPayload> = {}): DecisionsPayload {
  return {
    as_of: '2026-11-04T20:00:00+00:00',
    count: 3,
    records: [decisionRecord(), blockedCall(), yesterday],
    v2_verdicts_available: true,
    ...overrides,
  }
}

function list() {
  return screen.getByRole('list', { name: 'Decisions' })
}

function detail() {
  return screen.getByRole('region', { name: /Selected decision/ })
}

describe('Decisions page', () => {
  it("counts today's decisions in the summary chips", () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)

    const chips = screen.getByRole('group', { name: 'Decision summary' })
    expect(chips.textContent).toContain('2 evaluated')
    expect(chips.textContent).toContain('1 call')
    expect(chips.textContent).toContain('0 put')
    expect(chips.textContent).toContain('1 no trade')
    expect(chips.textContent).toContain('0 executed')
  })

  it('lists one button per decision with the strategist and manager verdicts and why', () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)

    const rows = within(list()).getAllByRole('button')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('DVN')
    expect(rows[0].textContent).toMatch(/S\s*Watchlist\s*→\s*M\s*No trade/)
    expect(rows[0].textContent).toContain('Strategist chose watchlist · medium confidence')
    expect(rows[1].textContent).toContain('Blocked by Execution: Premium cap hit')
  })

  it('opens on the newest decision and switches when another row is picked', async () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)
    expect(within(detail()).getByRole('heading', { name: 'DVN · No trade' })).toBeTruthy()
    expect(within(list()).getAllByRole('button')[0].getAttribute('aria-pressed')).toBe('true')

    await userEvent.click(within(list()).getAllByRole('button')[1])
    expect(within(detail()).getByRole('heading', { name: 'MSFT · Call' })).toBeTruthy()
    expect(detail().textContent).toContain('snapshot #3260')
    expect(detail().textContent).toContain('deepseek/deepseek-v4.1-flash-1017:nitro')
  })

  it('shows the decision path, marking where it stopped', () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)

    const path = within(detail()).getByRole('list', { name: 'Decision path' })
    const steps = within(path).getAllByRole('listitem').map((step) => step.textContent)
    expect(steps).toHaveLength(5)
    expect(steps[0]).toMatch(/Passed.*News.*1 article cited/)
    expect(steps[1]).toMatch(/Stopped here.*Strategist.*Watchlist/)
    expect(steps[2]).toMatch(/Not reached.*Manager.*Not asked/)
    expect(steps[3]).toMatch(/Also blocked.*Safeguards/)
  })

  it("puts the strategist's thesis and risks beside the manager's reason", () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)

    const strategist = within(detail()).getByRole('region', { name: /Strategist/ })
    expect(within(strategist).getByText('Positive EPS revisions into earnings.')).toBeTruthy()
    expect(within(strategist).getByText('The Nov 5 earnings report is a binary event.')).toBeTruthy()
    const manager = within(detail()).getByRole('region', { name: /Manager/ })
    expect(within(manager).getByText('The strategist answered watchlist; the manager was not asked.')).toBeTruthy()
  })

  it('asks what would have happened for a decision that did not trade, with the stock move pending', () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)

    const whatIf = within(detail()).getByRole('region', { name: /What if it had traded/ })
    expect(whatIf.textContent).toContain('5d Pending')
    expect(whatIf.textContent).toContain('10d Pending')
    expect(whatIf.textContent).toContain('Abstained')
  })

  it('fills in the stock move once attribution has scored it', () => {
    const scored = decisionRecord({
      attribution: { evidence_quality: 'weak', evidence_source: 'thesis_check', direction_outcome: 'abstained', expression_outcome: 'not_traded', primary_failure: 'none', horizon_returns: { '5': 0.0124, '10': -0.031 } },
    })
    render(<DecisionsView payload={payload({ records: [scored] })} notices={[]} initialQuery="" />)

    const whatIf = within(detail()).getByRole('region', { name: /What if it had traded/ })
    expect(within(whatIf).getByText('+1.24%').className).toContain('text-gain-text')
    expect(within(whatIf).getByText('-3.10%').className).toContain('text-loss-text')
  })

  it('shows the outcome instead for a decision that traded', () => {
    const traded = blockedCall({ v2_verdicts: [], outcome: { trade_executed: true, order_id: 'ord-1', pnl_pct: 12.5, resolved_outcome_label: 'take_profit' } })
    render(<DecisionsView payload={payload({ records: [traded] })} notices={[]} initialQuery="" />)

    expect(within(detail()).queryByRole('region', { name: /What if it had traded/ })).toBeNull()
    const outcome = within(detail()).getByRole('region', { name: /Outcome/ })
    expect(outcome.textContent).toContain('+12.50%')
    expect(outcome.textContent).toContain('Take profit')
    expect(outcome.textContent).toContain('ord-1')
  })

  it('lists each gate with its mode and result, enforced and shadow apart', async () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)
    await userEvent.click(within(list()).getAllByRole('button')[1])

    const gates = within(detail()).getByRole('table')
    const rows = within(gates).getAllByRole('row').map((row) => row.textContent)
    expect(rows.some((row) => /Evidence continuation.*Disabled.*Blocked/.test(row ?? ''))).toBe(true)
    expect(rows.some((row) => /Market regime.*Shadow.*Allowed/.test(row ?? ''))).toBe(true)
    expect(rows.some((row) => /Execution.*Enforced.*Blocked.*Premium cap hit/.test(row ?? ''))).toBe(true)
    expect(rows.some((row) => /Market regime scope \(G3\).*Shadow.*Would have blocked/.test(row ?? ''))).toBe(true)
  })

  it('says when the V2 verdicts could not be read', () => {
    render(<DecisionsView payload={payload({ v2_verdicts_available: false })} notices={[]} initialQuery="" />)
    expect(detail().textContent).toContain('V2 verdicts are not available')
  })

  it('gives each cited article its age when the decision ran', () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)

    const evidence = within(detail()).getByRole('region', { name: /Evidence cited/ })
    expect(within(evidence).getByText('What Just Happened To Devon Energy Stock?')).toBeTruthy()
    expect(evidence.textContent).toContain('22.3 h old')
  })

  it('filters by verdict and by the stage that stopped the decision', async () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)

    await userEvent.click(screen.getByRole('button', { name: 'Calls' }))
    expect(within(list()).getAllByRole('button').map((row) => row.textContent)).toEqual([expect.stringContaining('MSFT')])

    await userEvent.click(screen.getByRole('button', { name: 'All' }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Stopped at' }), 'strategist')
    expect(within(list()).getAllByRole('button')).toHaveLength(1)
  })

  it('searches all loaded history when a link brings a search', () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="pbf" />)

    expect((screen.getByRole('searchbox', { name: 'Search decisions' }) as HTMLInputElement).value).toBe('pbf')
    expect(within(list()).getAllByRole('button').map((row) => row.textContent)).toEqual([expect.stringContaining('PBF')])
  })

  it('opens on all loaded history when nothing ran today', () => {
    render(<DecisionsView payload={payload({ records: [yesterday] })} notices={[]} initialQuery="" />)

    expect(screen.getByRole('button', { name: 'All loaded' }).getAttribute('aria-pressed')).toBe('true')
    expect(within(list()).getAllByRole('button')).toHaveLength(1)
  })

  it('says so when no decision matches the filters', async () => {
    render(<DecisionsView payload={payload()} notices={[]} initialQuery="" />)

    await userEvent.click(screen.getByRole('button', { name: 'Puts' }))
    expect(screen.getByText('No decisions match these filters.')).toBeTruthy()
  })

  it('shows failures that left no decision behind as one warning line each', () => {
    render(<DecisionsView payload={payload()} notices={['AAPL: latest strategist attempt failed (4 articles supplied): bad JSON']} initialQuery="" />)
    expect(screen.getByText('AAPL: latest strategist attempt failed (4 articles supplied): bad JSON').className).toContain('text-warning')
  })
})
