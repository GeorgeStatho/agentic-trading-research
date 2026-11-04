import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { EquityPoint } from '../../equitySeries';
import { formatSignedCurrency } from '../../format';
import { chartTheme } from '../chartTheme';
import { AXIS_LINE, AXIS_TICK, TOOLTIP_STYLE, formatSessionDay } from './axis';

/** Each session's P/L as a green or red bar. */
export function DailyPnlChart({ points }: { points: readonly EquityPoint[] }) {
  const data = points.map((point) => ({ day: formatSessionDay(point.time), pnl: point.pnl }));
  const up = points.filter((point) => point.pnl > 0).length;
  const down = points.filter((point) => point.pnl < 0).length;

  return (
    <div role="img" aria-label={`${points.length} sessions: ${up} up, ${down} down`} className="h-32">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={chartTheme.grid} vertical={false} />
          <XAxis dataKey="day" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} />
          <YAxis width="auto" tick={AXIS_TICK} axisLine={false} tickLine={false}
            tickFormatter={(value: number) => (value === 0 ? '$0' : formatSignedCurrency(value, { digits: 0 }))} />
          <Tooltip {...TOOLTIP_STYLE} cursor={{ fill: chartTheme.grid }} formatter={(value) => formatSignedCurrency(Number(value))} />
          <ReferenceLine y={0} stroke={chartTheme.axis} />
          <Bar dataKey="pnl" name="P/L" isAnimationActive={false}>
            {data.map((entry) => <Cell key={entry.day} fill={entry.pnl >= 0 ? chartTheme.gain : chartTheme.loss} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
