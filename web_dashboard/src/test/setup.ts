import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Without vitest globals, Testing Library cannot register this itself.
afterEach(() => {
  cleanup()
})
