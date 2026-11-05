import { getDashboardKpis } from './dashboardKpisApi';
import { buildLiveStats } from './liveStats';
import { StatStrip } from './ui/StatStrip';
import { usePolledResource } from './useRemoteResource';

/** The Live page's runtime-stats strip. */
function DashboardKpis() {
  const { payload, error } = usePolledResource(getDashboardKpis, {
    errorMessage: 'Failed to load dashboard KPIs.',
  });

  if (error) {
    return <StatStrip items={[{ label: 'Account', value: 'Unavailable', sub: error, tone: 'muted' }]} />;
  }

  if (!payload) {
    return <StatStrip items={[{ label: 'Account', value: 'Loading…', tone: 'muted' }]} />;
  }

  return <StatStrip items={buildLiveStats(payload)} />;
}

export default DashboardKpis;
