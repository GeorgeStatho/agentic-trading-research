import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Collapse } from './Collapse.tsx'
import { DataTable } from './DataTable.tsx'
import { Meter } from './Meter.tsx'
import { Panel } from './Panel.tsx'
import { SearchInput } from './SearchInput.tsx'
import { Segmented } from './Segmented.tsx'
import { Select } from './Select.tsx'
import { StatStrip } from './StatStrip.tsx'
import { Tag } from './Tag.tsx'
import { TickerChip } from './TickerChip.tsx'

describe('Panel', () => {
  it('is a region named by its title, with meta, actions and body', () => {
    render(<Panel title="Orders" meta="5 recorded" actions={<button type="button">Refresh</button>}>body text</Panel>)

    const panel = screen.getByRole('region', { name: /Orders/ })
    expect(within(panel).getByRole('heading', { level: 2 }).textContent).toBe('Orders · 5 recorded')
    expect(within(panel).getByRole('button', { name: 'Refresh' })).toBeTruthy()
    expect(within(panel).getByText('body text')).toBeTruthy()
  })

  it('pads the body unless flush, so tables can run edge to edge', () => {
    const { rerender } = render(<Panel title="A">padded</Panel>)
    expect(screen.getByText('padded').className).toContain('p-3.5')

    rerender(<Panel title="A" flush>flush</Panel>)
    expect(screen.getByText('flush').className).not.toContain('p-3.5')
  })
})

describe('StatStrip', () => {
  it('shows each stat with its label, value and sub line', () => {
    render(<StatStrip items={[
      { label: 'Equity', value: '$2,379.77', sub: 'paper' },
      { label: 'Day P/L', value: '-$225.02', tone: 'loss' },
    ]} />)

    expect(screen.getByText('Equity')).toBeTruthy()
    expect(screen.getByText('$2,379.77')).toBeTruthy()
    expect(screen.getByText('paper')).toBeTruthy()
    expect(screen.getByText('-$225.02').className).toContain('text-loss')
  })

  it('mutes a stat whose sample is too small to trust', () => {
    render(<StatStrip items={[{ label: 'Win rate', value: '0%', sub: 'n=2', tone: 'muted' }]} />)
    expect(screen.getByText('0%').className).toContain('text-dim')
  })
})

describe('Tag', () => {
  it.each([
    ['gain', 'text-gain-text'],
    ['loss', 'text-loss-text'],
    ['neutral', 'text-ink-2'],
    ['accent', 'text-ticker'],
  ] as const)('uses the %s tone', (tone, colorClass) => {
    render(<Tag tone={tone}>Filled</Tag>)
    expect(screen.getByText('Filled').className).toContain(colorClass)
  })

  it('defaults to neutral, so color stays reserved for money and status', () => {
    render(<Tag>Not submitted</Tag>)
    expect(screen.getByText('Not submitted').className).toContain('text-ink-2')
  })
})

describe('TickerChip', () => {
  it('renders the symbol in monospace', () => {
    render(<TickerChip symbol="ORCL" />)
    expect(screen.getByText('ORCL').className).toContain('font-mono')
  })
})

describe('SearchInput', () => {
  it('is a labelled search box that reports each edit', async () => {
    const onChange = vi.fn()
    render(<SearchInput label="Search decisions" value="" onChange={onChange} />)

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search decisions' }), 'o')
    expect(onChange).toHaveBeenCalledWith('o')
  })
})

describe('Segmented', () => {
  const options = [
    { value: '1W', label: '1W' },
    { value: '1M', label: '1M' },
  ] as const

  it('marks the active option as pressed and reports a new choice', async () => {
    const onChange = vi.fn()
    render(<Segmented label="Chart range" options={options} value="1W" onChange={onChange} />)

    const group = screen.getByRole('group', { name: 'Chart range' })
    expect(within(group).getByRole('button', { name: '1W' }).getAttribute('aria-pressed')).toBe('true')
    expect(within(group).getByRole('button', { name: '1M' }).getAttribute('aria-pressed')).toBe('false')

    await userEvent.click(within(group).getByRole('button', { name: '1M' }))
    expect(onChange).toHaveBeenCalledWith('1M')
  })
})

describe('Select', () => {
  it('is labelled and reports the chosen value', async () => {
    const onChange = vi.fn()
    render(<Select label="Stopped at" value="any" onChange={onChange} options={[
      { value: 'any', label: 'Any' },
      { value: 'manager', label: 'Manager' },
    ]} />)

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Stopped at' }), 'manager')
    expect(onChange).toHaveBeenCalledWith('manager')
  })
})

describe('Meter', () => {
  it('shows progress toward the target as a bar and as n / max', () => {
    render(<Meter label="Closed trades" value={2} max={5} />)

    const bar = screen.getByRole('progressbar', { name: 'Closed trades' })
    expect(bar.getAttribute('value')).toBe('2')
    expect(bar.getAttribute('max')).toBe('5')
    expect(screen.getByText('2 / 5')).toBeTruthy()
  })
})

describe('Collapse', () => {
  it('hides the full text behind its title until opened', () => {
    const { container } = render(<Collapse title="Full manager reply">long text</Collapse>)

    const details = container.querySelector('details')
    expect(details?.open).toBe(false)
    expect(within(details as HTMLElement).getByText('Full manager reply').tagName).toBe('SUMMARY')
    expect(within(details as HTMLElement).getByText('long text')).toBeTruthy()
  })

  it('can start open', () => {
    const { container } = render(<Collapse title="Thesis" defaultOpen>text</Collapse>)
    expect(container.querySelector('details')?.open).toBe(true)
  })
})

describe('DataTable', () => {
  type Order = { id: string; contract: string; fill: string; status: string }
  const columns = [
    { key: 'contract', header: 'Contract', render: (row: Order) => row.contract },
    { key: 'fill', header: 'Fill', align: 'right' as const, render: (row: Order) => row.fill },
  ]
  const rows: Order[] = [
    { id: 'a', contract: 'ORCL 20 Nov $121P', fill: '$2.10', status: 'filled' },
    { id: 'b', contract: 'TSLA 27 Nov $415C', fill: '—', status: 'canceled' },
  ]

  it('renders a header and one row per item, right-aligning numeric columns', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(row) => row.id} />)

    expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Contract', 'Fill'])
    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText('$2.10').className).toContain('text-right')
    expect(screen.getByRole('columnheader', { name: 'Fill' }).className).toContain('text-right')
  })

  it('lets the caller mute rows such as canceled orders', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(row) => row.id}
      rowClassName={(row) => (row.status === 'canceled' ? 'opacity-60' : '')} />)

    expect(screen.getByText('TSLA 27 Nov $415C').closest('tr')?.className).toContain('opacity-60')
    expect(screen.getByText('ORCL 20 Nov $121P').closest('tr')?.className).not.toContain('opacity-60')
  })

  it('shows one full-width row with the empty message when there are no rows', () => {
    render(<DataTable columns={columns} rows={[]} rowKey={(row) => row.id} emptyMessage="No orders yet" />)

    const cell = screen.getByText('No orders yet')
    expect(cell.tagName).toBe('TD')
    expect(cell.getAttribute('colspan')).toBe('2')
  })

  it('scrolls sideways inside its own box on narrow screens', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(row) => row.id} />)
    expect(screen.getByRole('table').parentElement?.className).toContain('overflow-x-auto')
  })
})
