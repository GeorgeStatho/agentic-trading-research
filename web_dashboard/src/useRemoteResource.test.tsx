import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { usePolledResource } from './useRemoteResource.ts'

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: state })
  Object.defineProperty(document, 'hidden', { configurable: true, value: state === 'hidden' })
}

describe('usePolledResource', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setVisibility('visible')
  })

  afterEach(() => {
    vi.useRealTimers()
    setVisibility('visible')
  })

  it('skips polls while the tab is hidden', async () => {
    const load = vi.fn(() => Promise.resolve('payload'))
    renderHook(() => usePolledResource(load, { errorMessage: 'failed', intervalMs: 1000 }))
    expect(load).toHaveBeenCalledTimes(1)

    setVisibility('hidden')
    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('reads again as soon as the tab becomes visible', async () => {
    const load = vi.fn(() => Promise.resolve('payload'))
    renderHook(() => usePolledResource(load, { errorMessage: 'failed', intervalMs: 60_000 }))
    setVisibility('hidden')
    await act(async () => {
      vi.advanceTimersByTime(120_000)
    })
    const callsWhileHidden = load.mock.calls.length

    setVisibility('visible')
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(load).toHaveBeenCalledTimes(callsWhileHidden + 1)
  })

  it('stops listening for visibility changes after unmount', async () => {
    const load = vi.fn(() => Promise.resolve('payload'))
    const { unmount } = renderHook(() => usePolledResource(load, { errorMessage: 'failed', intervalMs: 1000 }))
    unmount()
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(load).toHaveBeenCalledTimes(1)
  })
})
