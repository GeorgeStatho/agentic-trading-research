import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { LatestDecisionView } from './whyBotTraded.tsx'
import type { WhyBotTradedPayload } from './whyBotTraded.tsx'

/** Today's `/api/why-bot-traded` payload: no strategist verdict, contract or exit plan yet. */
function payload(overrides: Partial<WhyBotTradedPayload> = {}): WhyBotTradedPayload {
  return {
    ran_at: '2026-11-03T20:38:25',
    has_decision: true,
    ticker: 'NVDA',
    decision: 'No Trade',
    confidence: 'low',
    reason: 'The strategist answered watchlist; the manager was not asked.',
    selected_contract: '',
    rejected_because: '',
    submitted: false,
    ...overrides,
  }
}

function panel() {
  return screen.getByRole('region', { name: /Latest decision/ })
}

describe('Latest decision panel', () => {
  it('leads with the ticker, the manager verdict and the full reason', () => {
    render(<LatestDecisionView payload={payload()} />)

    expect(within(panel()).getByText('NVDA')).toBeTruthy()
    expect(within(panel()).getByText('No Trade')).toBeTruthy()
    expect(within(panel()).getByText('The strategist answered watchlist; the manager was not asked.')).toBeTruthy()
    expect(within(panel()).getByText('low confidence')).toBeTruthy()
  })

  it('links to the full decision autopsy', () => {
    render(<LatestDecisionView payload={payload()} />)
    expect(within(panel()).getByRole('link', { name: 'Open autopsy' }).getAttribute('href')).toBe('#/decisions/autopsy')
  })

  it('says "Not submitted" until an order goes out, then "Submitted"', () => {
    const { rerender } = render(<LatestDecisionView payload={payload()} />)
    expect(within(panel()).getByText('Not submitted')).toBeTruthy()

    rerender(<LatestDecisionView payload={payload({ submitted: true, selected_contract: 'ORCL 2026-11-20 121P' })} />)
    expect(within(panel()).getByText('Submitted').className).toContain('text-gain-text')
  })

  it('shows the strategist verdict before the manager once the API sends it', () => {
    render(<LatestDecisionView payload={payload({ strategist_decision: 'watchlist' })} />)

    const path = within(panel()).getByText('Strategist').parentElement as HTMLElement
    expect(path.textContent).toMatch(/Strategist\s*Watchlist\s*→\s*Manager\s*No Trade/)
  })

  it('omits the strategist step when the API does not send it', () => {
    render(<LatestDecisionView payload={payload()} />)
    expect(within(panel()).queryByText('Strategist')).toBeNull()
  })

  it('shows the selected contract when there is one', () => {
    render(<LatestDecisionView payload={payload({ decision: 'Put', selected_contract: 'ORCL 2026-11-20 121P', submitted: true })} />)

    const contract = within(panel()).getByRole('group', { name: 'Selected contract' })
    expect(within(contract).getByText('ORCL 2026-11-20 121P')).toBeTruthy()
  })

  it('says "None selected" and why when no contract was chosen', () => {
    render(<LatestDecisionView payload={payload({ rejected_because: 'Spread too wide' })} />)

    const contract = within(panel()).getByRole('group', { name: 'Selected contract' })
    expect(within(contract).getByText('None selected')).toBeTruthy()
    expect(within(contract).getByText('Spread too wide')).toBeTruthy()
  })

  it('falls back to where the decision stopped when there is no rejection text', () => {
    render(<LatestDecisionView payload={payload({ stopped_at: 'strategist' })} />)

    const contract = within(panel()).getByRole('group', { name: 'Selected contract' })
    expect(within(contract).getByText('Stopped at the strategist')).toBeTruthy()
  })

  it('shows the contract details and exit plan once the API sends them', () => {
    render(<LatestDecisionView payload={payload({
      decision: 'Put',
      submitted: true,
      selected_contract: 'ORCL 2026-11-20 121P',
      contract: { symbol: 'ORCL261120P00121000', type: 'put', expiration_date: '2026-11-20', dte: 17, strike: 121 },
      quantity: 1,
      fill_price: 2.1,
      exit_plan: { take_profit_pct: 35, stop_loss_pct: -25, dte_bucket: '12-20 DTE' },
    })} />)

    const contract = within(panel()).getByRole('group', { name: 'Selected contract' })
    expect(contract.textContent).toContain('Put')
    expect(contract.textContent).toContain('2026-11-20 · 17 DTE')
    expect(contract.textContent).toContain('$2.10')
    expect(within(contract).getByText('+35%').className).toContain('text-gain-text')
    expect(within(contract).getByText('-25%').className).toContain('text-loss-text')
  })

  it('hides the contract detail rows the API does not send yet', () => {
    render(<LatestDecisionView payload={payload({ selected_contract: 'ORCL 2026-11-20 121P', submitted: true })} />)

    const contract = within(panel()).getByRole('group', { name: 'Selected contract' })
    expect(within(contract).queryByText('Fill')).toBeNull()
    expect(within(contract).queryByText('Take profit')).toBeNull()
  })

  it('shows for and against factors only when the manager gives them', () => {
    const { rerender } = render(<LatestDecisionView payload={payload()} />)
    expect(within(panel()).queryByText('For')).toBeNull()

    rerender(<LatestDecisionView payload={payload({ factors_for: ['Guidance raised'], factors_against: ['IV elevated'] })} />)
    expect(within(panel()).getByText('Guidance raised').className).toContain('text-gain-text')
    expect(within(panel()).getByText('IV elevated').className).toContain('text-loss-text')
  })

  it('names the last contract actually traded when nothing was selected', () => {
    render(<LatestDecisionView payload={payload({ last_traded_contract: 'ORCL 20 Nov $121P · closed -4.2%' })} />)
    expect(within(panel()).getByText('ORCL 20 Nov $121P · closed -4.2%')).toBeTruthy()
  })

  it('shows the API message when there is no decision yet', () => {
    render(<LatestDecisionView payload={payload({ has_decision: false, ticker: '', reason: 'No pipeline run yet today.' })} />)
    expect(within(panel()).getByText('No pipeline run yet today.')).toBeTruthy()
    expect(within(panel()).queryByRole('group', { name: 'Selected contract' })).toBeNull()
  })
})
