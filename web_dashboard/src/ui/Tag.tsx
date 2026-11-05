import type { ReactNode } from 'react'

export type TagTone = 'gain' | 'loss' | 'neutral' | 'accent' | 'info'

const TONE_CLASS: Record<TagTone, string> = {
  gain: 'bg-gain-bg text-gain-text',
  loss: 'bg-loss-bg text-loss-text',
  neutral: 'bg-raised text-ink-2',
  accent: 'bg-accent-bg text-ticker',
  info: 'bg-[#0e1f33] text-[#79b8ff]',
}

/** A small status pill. Gain and loss are for money and pass/block only. */
export function Tag({ tone = 'neutral', children }: { tone?: TagTone; children: ReactNode }) {
  return <span className={`badge badge-sm rounded-sm border-0 ${TONE_CLASS[tone]}`}>{children}</span>
}
