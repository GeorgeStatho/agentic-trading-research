import type { ReactNode } from 'react';

export type MiniStat = { label: string; value: ReactNode };

/** Two or three small figures side by side, for the narrow panels at the foot of Live. */
export function MiniStats({ items }: { items: readonly MiniStat[] }) {
  return (
    <dl className="grid grid-cols-3 gap-3">
      {items.map((item) => (
        <div key={item.label}>
          <dt className="text-[11px] text-dim">{item.label}</dt>
          <dd className="text-[15px] font-semibold">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
