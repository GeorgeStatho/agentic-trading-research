import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { locationFromHash } from './routes.tsx'
import { SystemHealthView } from './systemHealth.tsx'
import { ExitLadders } from './systemRisk.tsx'
import { kpiPayload } from './test/kpiFixture.ts'

describe('System › Health', () => {
  it('shows usage, the thesis validator and the research rankings that left Live', () => {
    render(<SystemHealthView payload={kpiPayload()} />)

    expect(screen.getByRole('region', { name: /People using it/ }).textContent).toContain('2 installs')
    expect(screen.getByRole('region', { name: /Thesis validator/ }).textContent).toContain('flip 12%')
    const rankings = screen.getByRole('region', { name: /Research rankings/ })
    expect(rankings.textContent).toContain('Technology')
    expect(rankings.textContent).toContain('Semiconductors')
  })
})

describe('System › Risk', () => {
  it('shows the take-profit and stop-loss ladders with their fallbacks', () => {
    render(<ExitLadders rules={kpiPayload().option_exit_rules} />)

    const ladders = screen.getByRole('region', { name: /Option exit ladders/ })
    expect(ladders.textContent).toContain('12-20 DTE: +35%')
    expect(ladders.textContent).toContain('12-20 DTE: -25%')
    expect(ladders.textContent).toContain('+45%')
    expect(ladders.textContent).toContain('-35%')
  })
})

describe('System route', () => {
  it('opens on Health and has a Risk view', () => {
    expect(locationFromHash('#/system')).toMatchObject({ route: { slug: 'system' }, tab: { slug: 'health' } })
    expect(locationFromHash('#/system/risk')).toMatchObject({ route: { slug: 'system' }, tab: { slug: 'risk' } })
  })

  it('holds Extraction, and the old Research address still opens it', () => {
    expect(locationFromHash('#/system/extraction')).toMatchObject({ route: { slug: 'system' }, tab: { slug: 'extraction' } })
    expect(locationFromHash('#/research/extraction')).toMatchObject({ route: { slug: 'system' }, tab: { slug: 'extraction' } })
  })
})
