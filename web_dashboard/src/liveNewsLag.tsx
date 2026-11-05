import { fetchPolledJson } from './api';
import { LagScatter } from './ui/charts/LagScatter';
import type { LagTrade } from './ui/charts/lagPoints';
import { Panel } from './ui/Panel';
import { usePolledResource } from './useRemoteResource';

type NewsLagSummary = {
  headline: { median_lag_hours: number | null; within_1h: number; dated_entries: number };
  trades: LagTrade[];
};

function getNewsLag(): Promise<NewsLagSummary> {
  return fetchPolledJson<NewsLagSummary>('/api/news-lag', 'news lag');
}

/** Live's view of the News lag research page: does trading the news sooner pay? */
function LiveNewsLag() {
  const { payload, error } = usePolledResource(getNewsLag, { errorMessage: 'Failed to load news lag.' });

  if (!payload) {
    return <Panel title="News lag vs. option P/L"><p className={error ? 'text-error' : 'text-dim'}>{error ?? 'Loading news lag…'}</p></Panel>;
  }

  const { headline } = payload;
  return (
    <Panel
      title="News lag vs. option P/L"
      meta={headline.median_lag_hours === null ? undefined : `median lag ${headline.median_lag_hours.toFixed(1)} h`}
      actions={<a href="#/research/news-lag" className="text-[11px]">Open</a>}
    >
      <LagScatter trades={payload.trades} medianLagHours={headline.median_lag_hours} />
      <p className="mt-2 text-[11px] text-dim">
        Shaded: the first hour after the news · {headline.within_1h} of {headline.dated_entries} dated entries were placed within it
      </p>
    </Panel>
  );
}

export default LiveNewsLag;
