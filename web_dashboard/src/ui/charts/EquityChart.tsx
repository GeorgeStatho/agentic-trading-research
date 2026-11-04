import { Area, CartesianGrid, ComposedChart, Line, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { EquityPoint, TradeMarker } from '../../equitySeries';
import { formatCurrency, formatSignedPercent } from '../../format';
import { chartTheme } from '../chartTheme';
import { AXIS_LINE, AXIS_TICK, TOOLTIP_STYLE, formatSessionDay } from './axis';

type EquityChartProps = {
  points: readonly EquityPoint[];
  markers: readonly TradeMarker[];
};

function takeaway(points: readonly EquityPoint[]): string {
  if (points.length === 0) {
    return 'No equity history yet';
  }
  const first = points[0].equity;
  const last = points[points.length - 1].equity;
  return `Equity ${formatCurrency(last)}, ${formatSignedPercent((last / first - 1) * 100)} over the range shown`;
}

/** Equity per session, straight segments between real points, with entries and exits marked. */
export function EquityChart({ points, markers }: EquityChartProps) {
  const data = points.map((point) => ({ day: formatSessionDay(point.time), equity: point.equity }));

  return (
    <div role="img" aria-label={takeaway(points)} className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 16, right: 36, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={chartTheme.grid} vertical={false} />
          <XAxis dataKey="day" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} />
          <YAxis width="auto" domain={['auto', 'auto']} tick={AXIS_TICK} axisLine={false} tickLine={false}
            tickFormatter={(value: number) => formatCurrency(value, { digits: 0 })} />
          <Tooltip {...TOOLTIP_STYLE} formatter={(value) => formatCurrency(Number(value))} />
          <Area type="linear" dataKey="equity" stroke="none" fill={chartTheme.accent} fillOpacity={0.08} isAnimationActive={false} />
          <Line type="linear" dataKey="equity" name="Equity" stroke={chartTheme.accent} strokeWidth={2} dot={false} isAnimationActive={false} />
          {markers.map((marker) => (
            <ReferenceDot
              key={`${marker.kind}-${marker.time}-${marker.label}`}
              x={formatSessionDay(marker.time)}
              y={marker.equity}
              r={4}
              fill={marker.kind === 'entry' ? chartTheme.panel : chartTheme.ink}
              stroke={marker.kind === 'entry' ? chartTheme.info : chartTheme.ink}
              strokeWidth={2}
              label={{ value: marker.label, position: 'top', fill: chartTheme.dim, fontSize: 10 }}
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
