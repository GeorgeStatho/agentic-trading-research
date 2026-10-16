import { fetchPolledJson } from './api';
import { usePolledResource } from './useRemoteResource';
import './riskControls.css';

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

function RiskControlsPanel() {
  const { payload, error } = usePolledResource(getRiskControls, {
    errorMessage: 'Failed to load risk controls.',
  });

  if (error) {
    return (
      <section className="risk-controls-panel">
        <div className="risk-controls-panel__header">
          <div>
            <p className="risk-controls-panel__eyebrow">Risk Controls</p>
            <h2>Risk controls unavailable</h2>
          </div>
        </div>
        <p className="risk-controls-panel__empty">{error}</p>
      </section>
    );
  }

  if (!payload) {
    return (
      <section className="risk-controls-panel">
        <div className="risk-controls-panel__header">
          <div>
            <p className="risk-controls-panel__eyebrow">Risk Controls</p>
            <h2>Loading current safeguards</h2>
          </div>
        </div>
        <p className="risk-controls-panel__empty">Reading the bot&apos;s active settings and code-backed limits.</p>
      </section>
    );
  }

  const missing = payload.controls.filter((control) => control.status !== 'configured').length;

  // Static configuration, so it starts collapsed and never pushes live state off screen.
  return (
    <details className="risk-controls-panel">
      <summary>
        <span className="risk-controls-panel__title">Risk settings</span>
        <span className="risk-controls-panel__summary-meta">
          {payload.controls.length} rules{missing ? ` · ${missing} not configured` : ''} · Read only
        </span>
      </summary>
      <div className="risk-controls-panel__table-wrap">
        <table className="risk-controls-table">
          <thead>
            <tr><th scope="col">Rule</th><th scope="col">Setting</th><th scope="col">What it does</th></tr>
          </thead>
          <tbody>
            {payload.controls.map((control) => (
              <tr key={control.label}>
                <th scope="row">{control.label}</th>
                <td className="risk-controls-table__value">
                  {control.value}
                  {control.status !== 'configured' ? <small className="risk-controls-table__missing">Not configured</small> : null}
                </td>
                <td className="risk-controls-table__detail">{control.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export default RiskControlsPanel;
