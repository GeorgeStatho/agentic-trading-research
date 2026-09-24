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

  return (
    <section className="risk-controls-panel">
      <div className="risk-controls-panel__header">
        <div>
          <p className="risk-controls-panel__eyebrow">Risk Controls</p>
          <h2>Current bot settings</h2>
        </div>
        <span className="risk-controls-panel__badge">Read only</span>
      </div>

      <div className="risk-controls-grid">
        {payload.controls.map((control) => (
          <article
            key={control.label}
            className={`risk-control-card risk-control-card--${control.status === 'configured' ? 'configured' : 'missing'}`}
          >
            <p className="risk-control-card__label">{control.label}</p>
            <p className="risk-control-card__value">{control.value}</p>
            <p className="risk-control-card__detail">{control.detail}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export default RiskControlsPanel;
