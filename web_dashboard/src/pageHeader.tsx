import type { ReactNode } from 'react'

type PageHeaderProps = {
  title: string
  description?: ReactNode
  /** One quiet line: counts, as-of time, refresh cadence. */
  meta?: ReactNode
  /** Page-level buttons, right-aligned on wide screens. */
  actions?: ReactNode
}

/** Every page opens with this plain header, never a hero card, so the data leads. */
function PageHeader({ title, description, meta, actions }: PageHeaderProps) {
  // Explicit margins: unconverted page roots restore browser margins on h1/p.
  return <header className="mb-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
    <div>
      <h1 className="m-0 text-lg font-bold text-base-content">{title}</h1>
      {description ? <p className="m-0 mt-0.5 max-w-[80ch] text-ink-2">{description}</p> : null}
      {meta ? <p className="m-0 mt-1 text-[11px] text-dim">{meta}</p> : null}
    </div>
    {actions ? <div className="flex flex-wrap justify-end gap-2">{actions}</div> : null}
  </header>
}

export default PageHeader
