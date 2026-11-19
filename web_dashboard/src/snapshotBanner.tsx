import { useEffect, useState } from 'react'

import { formatDateTime } from './format.ts'

/** Stands in for the live status line: the loops' state in a day-old copy would only mislead. */
export function SnapshotBanner() {
  const [capturedAt, setCapturedAt] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    fetch(`${import.meta.env.BASE_URL}data/manifest.json`)
      .then((response) => (response.ok ? response.json() as Promise<{ captured_at?: string }> : null))
      .then((manifest) => { if (active) setCapturedAt(manifest?.captured_at ?? null) })
      .catch(() => undefined)
    return () => { active = false }
  }, [])

  return <div role="status" aria-label="Snapshot" className="border-b border-base-300 bg-head">
    <p className="mx-auto max-w-[1400px] px-5 py-1.5 text-[11px] text-ink-2">
      Daily snapshot{capturedAt ? ` taken ${formatDateTime(capturedAt)}` : ''}. Positions, status and every figure are
      as of then, and only the default views are included: other filters show a load error.
    </p>
  </div>
}
