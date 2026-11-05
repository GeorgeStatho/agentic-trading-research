import { fetchPolledJson } from './api';
import type { Column } from './ui/DataTable';
import { DataTable } from './ui/DataTable';
import { Panel } from './ui/Panel';
import { usePolledResource } from './useRemoteResource';

type RiskControl = {
  label: string;
  value: string;
  detail: string;
  status: 'configured' | 'missing' | string;
};

type RiskControlsPayload = {
  controls: RiskControl[];
};

function getRiskControls(): Promise<RiskControlsPayload> {
  return fetchPolledJson<RiskControlsPayload>('/api/risk-controls', 'risk controls');
}

const COLUMNS: Column<RiskControl>[] = [
  { key: 'rule', header: 'Rule', render: (control) => <span className="font-medium">{control.label}</span> },
  { key: 'setting', header: 'Setting', render: (control) => <span className="block max-w-72 whitespace-normal">
    {control.value}
    {control.status !== 'configured' ? <span className="block text-[11px] text-warning">Not configured</span> : null}
  </span> },
  { key: 'what', header: 'What it does', render: (control) => <span className="block max-w-[60ch] whitespace-normal text-ink-2">{control.detail}</span> },
];

/** The risk settings the bot reads, each with what it does. */
export function RiskControlsView({ controls }: { controls: readonly RiskControl[] }) {
  const missing = controls.filter((control) => control.status !== 'configured').length;
  return (
    <Panel title="Risk settings" meta={`${controls.length} rules${missing ? ` · ${missing} not configured` : ''} · read only`} flush>
      <DataTable label="Risk settings" columns={COLUMNS} rows={controls} rowKey={(control) => control.label} />
    </Panel>
  );
}

function RiskControlsPanel() {
  const { payload, error } = usePolledResource(getRiskControls, { errorMessage: 'Failed to load risk controls.' });
  if (!payload) {
    return <Panel title="Risk settings"><p className={error ? 'text-error' : 'text-dim'}>{error ?? 'Reading the bot’s active settings and code-backed limits…'}</p></Panel>;
  }
  return <RiskControlsView controls={payload.controls} />;
}

export default RiskControlsPanel;
