/**
 * Snapshot mode: a static copy of the dashboard for GitHub Pages, built with `VITE_SNAPSHOT=1`.
 *
 * Every `/api/...` read comes from a JSON file a capture run saved next to the build (`data/<key>.json`), so the
 * copy needs no server. The key rule must match the capture script's `snapshot_key`; both are checked against
 * `test/contracts/snapshot-keys.json`.
 */
export const NOT_CAPTURED = 'Not in this snapshot: only the default views are captured.'

export function snapshotMode(): boolean {
  return import.meta.env.VITE_SNAPSHOT === '1'
}

/** `/api/x?b=2&a=1&ts=…` → `x_a_1_b_2`: path and sorted, decoded parameters without the cache-buster. */
export function snapshotKey(url: string): string {
  const parsed = new URL(url, 'http://snapshot.invalid')
  const path = parsed.pathname.replace(/^\/api\//, '')
  const pairs = [...parsed.searchParams]
    .filter(([key]) => key !== 'ts')
    .sort(([keyA, valueA], [keyB, valueB]) => (keyA === keyB ? compare(valueA, valueB) : compare(keyA, keyB)))
  const text = pairs.length ? `${path}?${pairs.map(([key, value]) => `${key}=${value}`).join('&')}` : path
  return text.replace(/[^A-Za-z0-9-]/g, '_')
}

function compare(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0
}

/** Routes `/api/...` requests to the saved files; a view that wasn't captured answers 404 with `NOT_CAPTURED`. */
export function installSnapshotFetch(base: string = import.meta.env.BASE_URL): void {
  const pagesFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!url.startsWith('/api/')) return pagesFetch(input, init)
    const response = await pagesFetch(`${base}data/${snapshotKey(url)}.json`)
    return response.ok ? response : Response.json({ error: NOT_CAPTURED }, { status: 404 })
  }
}
