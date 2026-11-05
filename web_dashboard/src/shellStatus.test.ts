import { describe, expect, it } from 'vitest'

import { componentPills, errorSummary, heartbeatMissing } from './shellStatus.ts'

describe('componentPills', () => {
  it('names each component in sentence case with its state', () => {
    const pills = componentPills([
      { name: 'News Collector', state: 'running' },
      { name: 'Worker', state: 'paused' },
      { name: 'EOD Sync', state: 'paused' },
      { name: 'DB Backup', state: 'paused' },
    ])

    expect(pills.map((pill) => pill.text)).toEqual([
      'News collector running',
      'Worker paused',
      'EOD sync paused',
      'DB backup paused',
    ])
    expect(pills.map((pill) => pill.tone)).toEqual(['running', 'paused', 'paused', 'paused'])
  })

  it('counts a starting component as running', () => {
    expect(componentPills([{ name: 'Worker', state: 'starting' }])[0]).toEqual({
      text: 'Worker starting',
      tone: 'running',
    })
  })

  it('flags a component that is in error or has errored recently', () => {
    const pills = componentPills([
      { name: 'Worker', state: 'error' },
      { name: 'Option Manager', state: 'paused', recent_error: true },
    ])

    expect(pills.map((pill) => pill.tone)).toEqual(['error', 'error'])
    expect(pills[1].text).toBe('Option manager paused')
  })

  it('says unknown when a component reports no state', () => {
    expect(componentPills([{ name: 'Worker', state: '' }])[0]).toEqual({ text: 'Worker unknown', tone: 'unknown' })
  })
})

describe('errorSummary', () => {
  it('says "No errors" when nothing is failing', () => {
    expect(errorSummary([{ name: 'Worker', state: 'paused' }])).toBe('No errors')
  })

  it('counts components in error or with a recent error', () => {
    expect(errorSummary([
      { name: 'Worker', state: 'error' },
      { name: 'EOD Sync', state: 'running' },
    ])).toBe('1 error')
    expect(errorSummary([
      { name: 'Worker', state: 'error' },
      { name: 'DB Backup', state: 'paused', recent_error: true },
    ])).toBe('2 errors')
  })
})

describe('heartbeatMissing', () => {
  const now = Date.parse('2026-11-04T15:40:00Z')

  it('is false while the worker reported within the last 90 seconds', () => {
    expect(heartbeatMissing({ state: 'paused', updated_at: '2026-11-04T15:39:00Z' }, false, now)).toBe(false)
  })

  it('is true once the last report is older than 90 seconds', () => {
    expect(heartbeatMissing({ state: 'running', updated_at: '2026-11-04T15:38:00Z' }, false, now)).toBe(true)
  })

  it('is true when the status cannot be read or has no usable time', () => {
    expect(heartbeatMissing(null, true, now)).toBe(true)
    expect(heartbeatMissing({ state: 'running', updated_at: 'garbage' }, false, now)).toBe(true)
  })

  it('is false before the first read finishes', () => {
    expect(heartbeatMissing(null, false, now)).toBe(false)
  })
})

describe('worker timestamps', () => {
  it('reads a timestamp without a zone as UTC, which is what the server writes', () => {
    const now = Date.parse('2026-11-04T07:16:00Z')
    expect(heartbeatMissing({ state: 'paused', updated_at: '2026-11-04T07:15:53.760331' }, false, now)).toBe(false)
    expect(heartbeatMissing({ state: 'paused', updated_at: '2026-11-04T07:10:00' }, false, now)).toBe(true)
  })
})
