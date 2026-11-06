import { describe, expect, it } from 'vitest'
import { expectancyPayload } from './expectancyFixture.ts'
import { kpiPayload } from './kpiFixture.ts'

// tests/test_api_contracts.py writes each contract from the real Python payload. A path in a typed
// fixture that the API no longer sends means the dashboard type is stale.
const CONTRACTS = import.meta.glob<string[]>('./contracts/*.json', { eager: true, import: 'default' })

function contract(name: string): Set<string> {
  const paths = CONTRACTS[`./contracts/${name}.json`]
  if (!paths) throw new Error(`No contract for ${name}: run UPDATE_API_CONTRACTS=1 python3 -m pytest tests/test_api_contracts.py`)
  return new Set(paths)
}

function keyPaths(value: unknown, prefix = ''): string[] {
  if (Array.isArray(value)) return value.flatMap((item) => keyPaths(item, `${prefix}[]`))
  if (value === null || typeof value !== 'object') return []
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return [path, ...keyPaths(child, path)]
  })
}

describe('API contracts', () => {
  it.each([
    ['decision-expectancy', expectancyPayload()],
    ['dashboard-kpis', kpiPayload()],
  ])('every path the %s fixture uses is in the API contract', (name, fixture) => {
    const known = contract(name)
    expect(keyPaths(fixture).filter((path) => !known.has(path))).toEqual([])
  })
})
