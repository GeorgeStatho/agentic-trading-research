import type { ReactNode } from 'react'

type PageHeaderProps = {
  /** Anchor for in-page navigation (the Overview sidebar links to its header). */
  id?: string
  className?: string
  title: string
  description?: ReactNode
  /** One quiet line: counts, as-of time, refresh cadence. */
  meta?: ReactNode
  /** Page-level buttons, right-aligned on wide screens. */
  actions?: ReactNode
}

/** Every page opens with this plain header, never a hero card, so the data leads. */
function PageHeader({ id, className, title, description, meta, actions }: PageHeaderProps) {
  return <header id={id} className={className ? `page-header ${className}` : 'page-header'}>
    <div>
      <h1>{title}</h1>
      {description ? <p className="page-header__description">{description}</p> : null}
      {meta ? <p className="page-header__meta">{meta}</p> : null}
    </div>
    {actions ? <div className="page-header__actions">{actions}</div> : null}
  </header>
}

export default PageHeader
