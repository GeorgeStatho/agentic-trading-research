export function TickerChip({ symbol }: { symbol: string }) {
  return <span className="rounded-sm bg-accent-bg px-1.5 py-0.5 font-mono text-[11px] font-semibold text-ticker">{symbol}</span>
}
