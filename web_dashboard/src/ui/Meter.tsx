type MeterProps = {
  label: string
  value: number
  max: number
  /** `success` once the target is met, e.g. a satisfied rollout prerequisite. */
  tone?: 'primary' | 'success'
}

/** Progress toward a sample-size or rollout target, with the count spelled out. */
export function Meter({ label, value, max, tone = 'primary' }: MeterProps) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between gap-2 text-[11px] text-dim">
        <span>{label}</span>
        <span>{value} / {max}</span>
      </div>
      <progress
        className={`progress h-2 ${tone === 'success' ? 'progress-success' : 'progress-primary'}`}
        aria-label={label}
        value={value}
        max={max}
      />
    </div>
  )
}
