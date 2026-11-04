import { CartesianGrid, Cell, ReferenceArea, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts';

import { chartTheme } from '../chartTheme';
import { AXIS_LINE, AXIS_TICK, TOOLTIP_STYLE } from './axis';
import { lagPoints } from './lagPoints';
import type { LagTrade } from './lagPoints';

type LagScatterProps = {
  trades: readonly LagTrade[];
  medianLagHours: number | null;
};

/** Hours from the newest cited article to our order, against how the option closed. */
export function LagScatter({ trades, medianLagHours }: LagScatterProps) {
  const points = lagPoints(trades);
  const withinHour = points.filter((point) => point.lag <= 1).length;
  const median = medianLagHours === null ? 'unknown' : `${medianLagHours.toFixed(1)} h`;

  return (
    <div role="img" aria-label={`Median news-to-order lag ${median}; ${withinHour} of ${points.length} plotted entries within the first hour`} className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 12, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={chartTheme.grid} />
          <XAxis type="number" dataKey="lag" name="Lag" unit=" h" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} />
          <YAxis type="number" dataKey="pnl" name="Option P/L" unit="%" width="auto" tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <ReferenceArea x1={0} x2={1} fill={chartTheme.accent} fillOpacity={0.06} />
          <ReferenceLine y={0} stroke={chartTheme.axis} />
          {medianLagHours !== null ? <ReferenceLine x={medianLagHours} stroke={chartTheme.dim} strokeDasharray="4 4" /> : null}
          <Tooltip {...TOOLTIP_STYLE} cursor={{ stroke: chartTheme.axis }} />
          <Scatter data={points} isAnimationActive={false}>
            {points.map((point) => <Cell key={`${point.symbol}-${point.lag}`} fill={point.pnl >= 0 ? chartTheme.gain : chartTheme.loss} />)}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
