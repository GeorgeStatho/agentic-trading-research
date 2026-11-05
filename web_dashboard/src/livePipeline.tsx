import { fetchPolledJson } from './api';
import { formatNumber, formatRate } from './format';
import { pipelineTotals } from './pipelineTotals';
import type { ExtractionWindow } from './pipelineTotals';
import { MiniStats } from './ui/MiniStats';
import { Panel } from './ui/Panel';
import { usePolledResource } from './useRemoteResource';

function getExtractionWeek(): Promise<ExtractionWindow> {
  return fetchPolledJson<ExtractionWindow>('/api/news-extraction?days=7', 'news extraction');
}

export function LivePipelineView({ extraction }: { extraction: ExtractionWindow }) {
  const totals = pipelineTotals(extraction);

  return (
    <Panel title="Data pipeline" meta={`${extraction.days} days`} actions={<a href="#/system/extraction" className="text-[11px]">Open</a>}>
      <MiniStats items={[
        { label: 'Extraction', value: formatRate(totals.successRate) },
        { label: 'URLs saved', value: formatNumber(totals.saved, { digits: 0 }) },
        { label: 'Sources', value: totals.sources },
      ]} />
    </Panel>
  );
}

function LivePipeline() {
  const { payload, error } = usePolledResource(getExtractionWeek, { errorMessage: 'Failed to load the news pipeline.' });

  if (!payload) {
    return <Panel title="Data pipeline"><p className={error ? 'text-error' : 'text-dim'}>{error ?? 'Loading…'}</p></Panel>;
  }
  return <LivePipelineView extraction={payload} />;
}

export default LivePipeline;
