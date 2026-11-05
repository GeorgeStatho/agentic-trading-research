import { getDashboardKpis } from './dashboardKpisApi';
import type { DashboardKpiPayload } from './dashboardKpisApi';
import { formatRate } from './format';
import { MiniStats } from './ui/MiniStats';
import { Panel } from './ui/Panel';
import { usePolledResource } from './useRemoteResource';
import { getV2Status, safeguardNotices } from './v2StatusApi';
import type { V2StatusPayload } from './v2StatusApi';

type LiveSafeguardsViewProps = {
  v2: V2StatusPayload;
  validator: DashboardKpiPayload['thesis_validator'] | null;
};

/** Which V2 controls actually block trades, and how often the thesis validator changes its mind. */
export function LiveSafeguardsView({ v2, validator }: LiveSafeguardsViewProps) {
  const modes = v2.mode_summary;

  return (
    <Panel title="Safeguards" actions={<a href="#/research/rollout" className="text-[11px]">See the evidence</a>}>
      <MiniStats items={[
        { label: 'V2 enforcing', value: `${modes.enforcing} / ${v2.features.length}` },
        { label: 'Downgraded', value: modes.downgraded },
        { label: 'Thesis flip', value: validator?.available ? formatRate(validator.flip_rate) : '—' },
      ]} />
      {safeguardNotices(v2).map((notice) => <p key={notice} className="mt-2 text-xs text-warning">{notice}</p>)}
    </Panel>
  );
}

function LiveSafeguards() {
  const v2 = usePolledResource(getV2Status, { errorMessage: 'Failed to load safeguards.' });
  const kpis = usePolledResource(getDashboardKpis, { errorMessage: 'Failed to load the thesis validator.' });

  if (!v2.payload) {
    return <Panel title="Safeguards"><p className={v2.error ? 'text-error' : 'text-dim'}>{v2.error ?? 'Loading…'}</p></Panel>;
  }
  return <LiveSafeguardsView v2={v2.payload} validator={kpis.payload?.thesis_validator ?? null} />;
}

export default LiveSafeguards;
