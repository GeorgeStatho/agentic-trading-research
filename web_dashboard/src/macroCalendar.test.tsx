import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MacroCalendarStrip } from './macroCalendar.tsx'
import { macroCalendarPayload } from './test/reportFixtures.ts'

let body = macroCalendarPayload()

beforeEach(() => {
  body = macroCalendarPayload()
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(body)))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Coming up', () => {
  it('lists the medium and high importance events in time order', async () => {
    render(<MacroCalendarStrip />)
    const list = await screen.findByRole('list', { name: 'Coming up' })
    const items = within(list).getAllByRole('listitem').map((item) => item.textContent ?? '')

    expect(items).toHaveLength(4)
    expect(items[0]).toContain('ISM Services PMI')
    expect(items.join(' ')).not.toContain('Goods Trade Balance')
  })

  it('marks high-importance US events', async () => {
    render(<MacroCalendarStrip />)
    const list = await screen.findByRole('list', { name: 'Coming up' })
    const nfp = within(list).getByText('Non-Farm Employment Change').closest('li')!

    expect(nfp.className).toContain('text-warning')
    expect(within(list).getByText('German Factory Orders m/m').closest('li')!.className).not.toContain('text-warning')
  })

  it('says how far the calendar runs when nothing is left', async () => {
    body = { ...macroCalendarPayload(), events: [] }
    render(<MacroCalendarStrip />)
    expect(await screen.findByText(/Nothing scheduled through/)).toBeTruthy()
  })
})
