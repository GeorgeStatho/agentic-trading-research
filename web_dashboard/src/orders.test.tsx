import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ExecutedTrade } from './executedTradesApi.ts'
import { formatOccSymbol } from './occ.ts'
import { OrdersView } from './orderCard.tsx'
import { groupOrders, orderTypeLabel } from './orders.ts'

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
  it('downloads every recorded order as CSV', () => {
    render(<OrdersView trades={[]} />)
    expect(screen.getByRole('link', { name: 'Download CSV' }).getAttribute('href')).toBe('/api/executed-trades?limit=500&format=csv')
  })

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

describe('groupOrders', () => {
  const orders = [
    trade({ id: 4, order_id: 'exit', order_side: 'sell', decision: 'trailing_profit_stop', fill_price: 5.65, position_id: 'entry', submitted_at: '2026-11-03T14:41:00+00:00' }),
    trade({ id: 3, order_id: 'cancel', order_side: 'sell', decision: 'trailing_profit_stop', order_status: 'canceled', position_id: 'entry', submitted_at: '2026-11-03T14:31:00+00:00' }),
    trade({ id: 2, order_id: 'loose', order_side: 'sell', decision: 'stop_loss', position_id: null }),
    trade({ id: 1, order_id: 'entry', fill_price: 5.9, position_id: 'entry' }),
  ]

  it('folds an entry, its exit and its canceled exit attempts into one trade, newest activity first', () => {
    const groups = groupOrders(orders)

    expect(groups).toHaveLength(2)
    expect(groups[0].entry?.order_id).toBe('entry')
    expect(groups[0].exit?.order_id).toBe('exit')
    expect(groups[0].canceled).toBe(1)
    expect(groups[1].orders.map((order) => order.order_id)).toEqual(['loose'])
  })

  it('computes the trade return from the two fills', () => {
    expect(groupOrders(orders)[0].returnPct).toBeCloseTo(-4.237, 2)
  })

  it('leaves an open position without an exit or a return', () => {
    const [open] = groupOrders([trade({ order_id: 'entry', fill_price: 5.9, position_id: 'entry' })])
    expect(open.exit).toBeNull()
    expect(open.returnPct).toBeNull()
  })
})

describe('Orders panel with linked trades', () => {
  it('shows each trade on one row: both fills, the return and the canceled attempts', () => {
    render(<OrdersView trades={[
      trade({ id: 3, order_id: 'exit', order_side: 'sell', decision: 'trailing_profit_stop', fill_price: 5.65, position_id: 'entry' }),
      trade({ id: 2, order_id: 'cancel', order_side: 'sell', decision: 'trailing_profit_stop', order_status: 'canceled', position_id: 'entry' }),
      trade({ id: 1, order_id: 'entry', fill_price: 5.9, position_id: 'entry' }),
    ]} />)

    const rows = screen.getAllByRole('row').slice(1)
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain('$5.90 → $5.65')
    expect(rows[0].textContent).toContain('-4.24%')
    expect(rows[0].textContent).toContain('Trailing profit stop · sell')
    expect(rows[0].textContent).toContain('1 canceled attempt')
    expect(screen.getByRole('region', { name: /Orders/ }).textContent).toContain('1 trade · 3 orders')
  })
})
