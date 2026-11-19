import { snapshotMode } from '../snapshot.ts'

/** Downloads what a table shows as CSV; the API serves it with `format=csv`. A snapshot has no CSVs. */
export function CsvLink({ href }: { href: string }) {
  if (snapshotMode()) return null
  return <a href={href} download className="btn btn-ghost btn-xs font-normal text-ink-2">Download CSV</a>
}
