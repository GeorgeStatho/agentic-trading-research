import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

import { forgetSharedDashboardKpis } from '../dashboardKpisApi.ts'

// Without vitest globals, Testing Library cannot register this itself.
afterEach(() => {
  cleanup()
  forgetSharedDashboardKpis()
})

// jsdom has no layout, so Recharts' ResponsiveContainer needs a stand-in observer.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= NoopResizeObserver
