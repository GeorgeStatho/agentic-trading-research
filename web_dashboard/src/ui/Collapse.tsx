import type { ReactNode } from 'react'

type CollapseProps = {
  title: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}

/** Full-length text (strategist or manager replies) folded under a one-line title. */
export function Collapse({ title, defaultOpen = false, children }: CollapseProps) {
  return (
    <details className="collapse collapse-arrow rounded-box border border-base-300" open={defaultOpen}>
      <summary className="collapse-title min-h-0 px-3.5 py-2 text-xs font-semibold">{title}</summary>
      <div className="collapse-content px-3.5 text-ink-2">{children}</div>
    </details>
  )
}
