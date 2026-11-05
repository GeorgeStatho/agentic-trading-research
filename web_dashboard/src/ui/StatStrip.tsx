import type { ReactNode } from 'react'

export type Stat = {
  label: string
  value: ReactNode
  sub?: ReactNode
  /** `muted` is for figures from a sample too small to trust. */
  tone?: 'gain' | 'loss' | 'muted'
}

const TONE_CLASS = { gain: 'text-gain', loss: 'text-loss', muted: 'text-dim' }

/** One row of headline numbers, divided by thin rules rather than boxed as cards. */
export function StatStrip({ items }: { items: readonly Stat[] }) {
  return (
    <section className="flex flex-wrap rounded-box border border-base-300 bg-base-200">
      {items.map((stat) => (
        <div key={stat.label} className="min-w-[120px] flex-1 border-r border-raised px-4 py-3 last:border-r-0">
          <div className="text-[11px] text-dim">{stat.label}</div>
          <div className={`mt-0.5 text-[15px] font-semibold ${stat.tone ? TONE_CLASS[stat.tone] : ''}`}>{stat.value}</div>
          {stat.sub ? <div className="text-[11px] text-dim">{stat.sub}</div> : null}
        </div>
      ))}
    </section>
  )
}
