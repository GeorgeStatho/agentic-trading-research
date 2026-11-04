import type { ReactNode } from 'react'

type SegmentedProps<T extends string> = {
  label: string
  options: readonly { value: T; label: ReactNode }[]
  value: T
  onChange: (value: T) => void
}

/** A row of joined toggle buttons for picking one of a few values. */
export function Segmented<T extends string>({ label, options, value, onChange }: SegmentedProps<T>) {
  return (
    <div role="group" aria-label={label} className="join">
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            className={`btn join-item btn-xs ${active ? 'btn-active border-accent-line bg-accent-bg text-primary' : ''}`}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
