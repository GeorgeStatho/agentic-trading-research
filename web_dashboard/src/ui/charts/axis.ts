import { chartTheme } from '../chartTheme';

/** Axis, grid and tooltip styling every chart shares. */
export const AXIS_TICK = { fill: chartTheme.dim, fontSize: 11 } as const;
export const AXIS_LINE = { stroke: chartTheme.axis } as const;
export const TOOLTIP_STYLE = {
  contentStyle: { background: chartTheme.panel, border: `1px solid ${chartTheme.axis}`, borderRadius: 6, fontSize: 12 },
  labelStyle: { color: chartTheme.ink },
  itemStyle: { color: chartTheme.ink },
} as const;

const sessionDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'America/New_York' });

/** "Oct 29": the trading day a session timestamp belongs to. */
export function formatSessionDay(time: number): string {
  return sessionDay.format(time);
}
