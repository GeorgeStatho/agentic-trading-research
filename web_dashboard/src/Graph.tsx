import { useState } from 'react';

import { equityPoints, getPortfolioHistory, pointsInRange, tradeMarkers } from './equitySeries';
import type { ChartRange } from './equitySeries';
import { getExecutedTrades } from './executedTradesApi';
import { Panel } from './ui/Panel';
import { Segmented } from './ui/Segmented';
import { DailyPnlChart } from './ui/charts/DailyPnlChart';
import { DrawdownChart } from './ui/charts/DrawdownChart';
import { EquityChart } from './ui/charts/EquityChart';
import { usePolledResource } from './useRemoteResource';

const RANGES = [
  { value: '1W', label: '1W' },
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: 'All', label: 'All' },
] as const satisfies readonly { value: ChartRange; label: string }[];

function Legend() {
  return (
    <span className="flex items-center gap-3 text-[11px] text-dim" aria-hidden="true">
      <span className="flex items-center gap-1"><span className="h-0.5 w-3 bg-primary" />Equity</span>
      <span className="flex items-center gap-1"><span className="size-2 rounded-full border-2 border-info" />Entry</span>
      <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-base-content" />Exit</span>
    </span>
  );
}

/** The Live equity row: equity with trade markers, and drawdown and daily P/L from the same sessions. */
function Graph() {
  const [range, setRange] = useState<ChartRange>('1W');
  const history = usePolledResource(getPortfolioHistory, { errorMessage: 'Failed to load equity history.' });
  const trades = usePolledResource(getExecutedTrades, { errorMessage: 'Failed to load trades.' });

  const allPoints = history.payload ? equityPoints(history.payload) : [];
  const points = pointsInRange(allPoints, range);
  const markers = tradeMarkers(points, trades.payload?.trades ?? []);

  if (!history.payload) {
    return (
      <Panel title="Strategy equity">
        <p className={history.error ? 'text-error' : 'text-dim'}>{history.error ?? 'Loading equity history…'}</p>
      </Panel>
    );
  }

  return (
    <div className="flex flex-wrap gap-3">
      <Panel
        title="Strategy equity"
        className="flex-[2_1_640px]"
        actions={<><Legend /><Segmented label="Chart range" options={RANGES} value={range} onChange={setRange} /></>}
      >
        {points.length ? <EquityChart points={points} markers={markers} /> : <p className="text-dim">The account has not been funded yet.</p>}
        <p className="mt-2 text-[11px] text-dim">Trading sessions only; weekends and holidays are skipped.</p>
      </Panel>
      <div className="flex min-w-0 flex-[1_1_360px] flex-col gap-3">
        <Panel title="Drawdown">
          <DrawdownChart points={points} />
        </Panel>
        <Panel title="Daily P/L" meta={`last ${points.length} sessions`}>
          <DailyPnlChart points={points} />
        </Panel>
      </div>
    </div>
  );
}

export default Graph;
