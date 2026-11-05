import { describe, expect, it } from 'vitest'

import { asUtcTimestamp, formatDateTime } from './format.ts'

describe('server timestamps', () => {
  it('reads a date-time without a zone as UTC, which is what the API writes', () => {
    expect(formatDateTime('2026-11-03T20:38:25.821964')).toBe(formatDateTime('2026-11-03T20:38:25.821964Z'))
    expect(formatDateTime('2026-11-03 14:41:09')).toBe(formatDateTime('2026-11-03T14:41:09Z'))
  })

  it('leaves timestamps that name their zone alone', () => {
    expect(asUtcTimestamp('2026-11-04T07:15:53Z')).toBe('2026-11-04T07:15:53Z')
    expect(asUtcTimestamp('2026-11-04T02:15:53-05:00')).toBe('2026-11-04T02:15:53-05:00')
    expect(asUtcTimestamp('2026-11-03T14:41:09.154258+00:00')).toBe('2026-11-03T14:41:09.154258+00:00')
  })

  it('adds the zone to bare date-times, including the space-separated form', () => {
    expect(asUtcTimestamp('2026-11-04T07:15:53.76')).toBe('2026-11-04T07:15:53.76Z')
    expect(asUtcTimestamp('2026-11-03 14:41:09')).toBe('2026-11-03T14:41:09Z')
  })

  it('leaves plain dates as they were', () => {
    expect(asUtcTimestamp('2026-11-20')).toBe('2026-11-20')
  })
})
