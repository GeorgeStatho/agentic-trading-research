import { afterEach, describe, expect, it, vi } from 'vitest'

import { forgetSharedDashboardKpis, getDashboardKpis } from './dashboardKpisApi.ts'
import { kpiPayload } from './test/kpiFixture.ts'

function stubFetch(response: () => Response) {
  const fetchMock = vi.fn(async () => response())
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('getDashboardKpis', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    forgetSharedDashboardKpis()
  })

  it('makes one request when several panels ask at once', async () => {
    const fetchMock = stubFetch(() => Response.json(kpiPayload()))

    const [first, second, third] = await Promise.all([getDashboardKpis(), getDashboardKpis(), getDashboardKpis()])
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(second).toBe(first)
    expect(third).toBe(first)
  })

  it('reuses a fresh answer, and asks again once it is 15 seconds old', async () => {
    vi.useFakeTimers()
    const fetchMock = stubFetch(() => Response.json(kpiPayload()))

    await getDashboardKpis()
    vi.advanceTimersByTime(14_000)
    await getDashboardKpis()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(2_000)
    await getDashboardKpis()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not hold on to a failure', async () => {
    const fetchMock = stubFetch(() => new Response('timeout', { status: 504 }))

    await expect(getDashboardKpis()).rejects.toThrow('504')
    await expect(getDashboardKpis()).rejects.toThrow('504')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
