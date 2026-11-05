import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { drawdownPct } from '../../equitySeries';
import type { EquityPoint } from '../../equitySeries';
import { formatPercent } from '../../format';
import { chartTheme } from '../chartTheme';
import { AXIS_LINE, AXIS_TICK, TOOLTIP_STYLE, formatSessionDay } from './axis';

/** How far below its running peak the account sat at each session. */
export function DrawdownChart({ points }: { points: readonly EquityPoint[] }) {
  const drawdowns = drawdownPct(points);
  const data = points.map((point, index) => ({ day: formatSessionDay(point.time), drawdown: drawdowns[index] }));
  const deepest = Math.abs(Math.min(0, ...drawdowns));

  return (
    <div role="img" aria-label={`Max drawdown ${formatPercent(deepest)} over the range shown`} className="h-32">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={chartTheme.grid} vertical={false} />
          <XAxis dataKey="day" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} />
          <YAxis width="auto" domain={['auto', 0]} tick={AXIS_TICK} axisLine={false} tickLine={false}
            tickFormatter={(value: number) => `${value}%`} />
          <Tooltip {...TOOLTIP_STYLE} formatter={(value) => formatPercent(Number(value))} />
          <Area type="linear" dataKey="drawdown" name="Drawdown" stroke={chartTheme.loss} fill={chartTheme.loss} fillOpacity={0.15} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
