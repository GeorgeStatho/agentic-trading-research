import type { ReactNode } from 'react'

/**
 * The shell's line icons.
 *
 * Stroke, fill and size come from the CSS that wraps each icon, so the paths
 * here carry geometry only.
 */

export type IconName =
  | 'bell'
  | 'settings'
  | 'user'
  | 'dashboard'
  | 'chart'
  | 'shield'
  | 'feed'
  | 'note'
  | 'trend'
  | 'briefcase'
  | 'history'

const PATHS: Record<IconName, ReactNode> = {
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M10 21h4" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.12 2.12-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.04 1.56V20h-3v-.08A1.7 1.7 0 0 0 10.66 18a1.7 1.7 0 0 0-1.88.34l-.06.06L6.6 16.28l.06-.06A1.7 1.7 0 0 0 7 14.34 1.7 1.7 0 0 0 5.44 13.3H5v-3h.44A1.7 1.7 0 0 0 7 9.26a1.7 1.7 0 0 0-.34-1.88l-.06-.06L8.72 5.2l.06.06A1.7 1.7 0 0 0 10.66 5a1.7 1.7 0 0 0 1.04-1.56V3h3v.44A1.7 1.7 0 0 0 15.74 5a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.12 2.12-.06.06A1.7 1.7 0 0 0 19.4 8.66 1.7 1.7 0 0 0 20.96 9.7H21v3h-.04A1.7 1.7 0 0 0 19.4 15Z" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c.8-4.1 3.5-6 8-6s7.2 1.9 8 6" /></>,
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  chart: <><path d="M3 21h18" /><rect x="5" y="11" width="3.5" height="7" rx="1" /><rect x="10.25" y="5" width="3.5" height="13" rx="1" /><rect x="15.5" y="14" width="3.5" height="4" rx="1" /></>,
  shield: <path d="M12 3 20 6v5c0 5.3-3.4 8.9-8 10-4.6-1.1-8-4.7-8-10V6l8-3Z" />,
  feed: <><path d="M4 11a9 9 0 0 1 9 9" /><path d="M4 4a16 16 0 0 1 16 16" /><circle cx="5" cy="19" r="1" /></>,
  note: <><path d="M5 3h12l3 3v15H5z" /><path d="M9 11h6M9 15h6M9 7h2" /></>,
  trend: <><path d="M3 17l5.5-5.5 3.5 3.5L21 6" /><path d="M15 6h6v6" /></>,
  briefcase: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" /><path d="M3 12h18" /></>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5M12 7v5l3 2" /></>,
}

export function Icon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true">{PATHS[name]}</svg>
}
