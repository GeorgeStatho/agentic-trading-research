import { describe, expect, it, vi } from 'vitest'

// Only the exposure map draws a graph; the rest of the dashboard must load without it.
vi.mock('@xyflow/react', () => {
  throw new Error('@xyflow/react was loaded eagerly')
})

describe('routes', () => {
  it('loads without the exposure map graph library', async () => {
    const routes = await import('./routes.tsx')
    expect(routes.ROUTES.length).toBeGreaterThan(0)
  }, 30_000)
})
