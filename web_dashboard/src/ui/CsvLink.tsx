/** Downloads what a table shows as CSV; the API serves it with `format=csv`. */
export function CsvLink({ href }: { href: string }) {
  return <a href={href} download className="btn btn-ghost btn-xs font-normal text-ink-2">Download CSV</a>
}
