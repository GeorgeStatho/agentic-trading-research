import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Without vitest globals, Testing Library cannot register this itself.
afterEach(() => {
  cleanup()
})

// jsdom has no layout, so Recharts' ResponsiveContainer needs a stand-in observer.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= NoopResizeObserver
