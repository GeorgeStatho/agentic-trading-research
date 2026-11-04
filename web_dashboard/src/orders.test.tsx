import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ExecutedTrade } from './executedTradesApi.ts'
import { formatOccSymbol } from './occ.ts'
import { OrdersView } from './orderCard.tsx'
import { orderTypeLabel } from './orders.ts'

function trade(overrides: Partial<ExecutedTrade>): ExecutedTrade {
  return {
    id: 1,
    order_id: 'a',
    underlying_symbol: 'ORCL',
    company_name: 'Oracle Corporation',
    option_symbol: 'ORCL261120P00121000',
    decision: 'put',
    order_side: 'buy',
    order_qty: 1,
    expiration_date: '2026-11-20',
    strike_price: 121,
    order_status: 'filled',
    submitted_at: '2026-11-02T17:30:00+00:00',
    error: null,
    ...overrides,
  }
}

describe('formatOccSymbol', () => {
  it('reads an OCC option symbol as ticker, expiry, strike and side', () => {
    expect(formatOccSymbol('ORCL261120P00121000')).toBe('ORCL 20 Nov $121P')
    expect(formatOccSymbol('TSLA261127C00415000')).toBe('TSLA 27 Nov $415C')
  })

  it('keeps fractional strikes', () => {
    expect(formatOccSymbol('SPY261120C00512500')).toBe('SPY 20 Nov $512.5C')
  })

  it('returns anything that is not an OCC symbol unchanged', () => {
    expect(formatOccSymbol('ORCL')).toBe('ORCL')
  })
})

describe('orderTypeLabel', () => {
  it('names entries by side and contract type', () => {
    expect(orderTypeLabel(trade({}))).toBe('Entry · buy put')
    expect(orderTypeLabel(trade({ decision: 'call' }))).toBe('Entry · buy call')
  })

  it('names exits by the rule that closed them', () => {
    expect(orderTypeLabel(trade({ order_side: 'sell', decision: 'trailing_profit_stop' }))).toBe('Trailing profit stop · sell')
    expect(orderTypeLabel(trade({ order_side: 'sell', decision: 'stop_loss' }))).toBe('Stop loss · sell')
  })
})

describe('Orders panel', () => {
  const trades = [
    trade({ id: 3, order_side: 'sell', decision: 'trailing_profit_stop', order_status: 'canceled', submitted_at: '2026-11-03T14:31:00+00:00' }),
    trade({ id: 2, order_side: 'sell', decision: 'trailing_profit_stop', submitted_at: '2026-11-03T14:41:00+00:00' }),
    trade({ id: 1 }),
  ]

  it('lists every order with its formatted contract and type', () => {
    render(<OrdersView trades={trades} />)

    const panel = screen.getByRole('region', { name: /Orders/ })
    expect(panel.textContent).toContain('3 recorded')
    expect(within(panel).getAllByText('ORCL 20 Nov $121P')).toHaveLength(3)
    expect(within(panel).getByText('Entry · buy put')).toBeTruthy()
  })

  it('dims canceled orders and tags filled ones', () => {
    render(<OrdersView trades={trades} />)

    const rows = screen.getAllByRole('row').slice(1)
    expect(rows[0].className).toContain('opacity-60')
    expect(rows[1].className).not.toContain('opacity-60')
    expect(within(rows[1]).getByText('Filled').className).toContain('text-gain-text')
  })

  it('leaves out the fill column until the API returns fill prices', () => {
    const { rerender } = render(<OrdersView trades={trades} />)
    expect(screen.queryByRole('columnheader', { name: 'Fill' })).toBeNull()

    rerender(<OrdersView trades={[trade({ fill_price: 2.1 })]} />)
    expect(screen.getByRole('columnheader', { name: 'Fill' })).toBeTruthy()
    expect(screen.getByText('$2.10')).toBeTruthy()
  })
})
