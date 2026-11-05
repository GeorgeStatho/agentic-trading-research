import { useId } from 'react'
import type { ReactNode } from 'react'

type PanelProps = {
  title: ReactNode
  /** Quiet context after the title: a count, an as-of time. */
  meta?: ReactNode
  /** Controls on the right of the header bar: search, filters, links. */
  actions?: ReactNode
  /** Drops the body padding so a table's rows run edge to edge. */
  flush?: boolean
  className?: string
  children: ReactNode
}

/** A bordered panel with a thin header bar; the one container every page uses. */
export function Panel({ title, meta, actions, flush, className = '', children }: PanelProps) {
  const titleId = useId()

  return (
    <section aria-labelledby={titleId} className={`min-w-0 rounded-box border border-base-300 bg-base-200 ${className}`}>
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-base-300 px-3.5 py-2">
        <h2 id={titleId} className="text-xs font-semibold">
          {title}{meta ? <span className="font-normal text-dim"> · {meta}</span> : null}
        </h2>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </header>
      <div className={flush ? '' : 'p-3.5'}>{children}</div>
    </section>
  )
}
