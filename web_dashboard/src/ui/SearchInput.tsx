type SearchInputProps = {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  /** Shows the label above the box; otherwise it is only announced. */
  showLabel?: boolean
}

export function SearchInput({ label, value, onChange, placeholder, showLabel = false }: SearchInputProps) {
  const input = (
    <input
      type="search"
      className="input input-sm w-56 bg-base-100"
      aria-label={showLabel ? undefined : label}
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
    />
  )

  if (!showLabel) {
    return input
  }

  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-dim">{label}</span>
      {input}
    </label>
  )
}
