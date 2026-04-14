import { useEffect, useState } from 'react';
import './riskControls.css';

type RiskControl = {
  label: string;
  value: string;
  detail: string;
  status: 'configured' | 'missing' | string;
  source: string;
};

type RiskControlsPayload = {
  controls: RiskControl[];
};

const RISK_CONTROLS_POLL_INTERVAL_MS = 60_000;

async function getRiskControls(): Promise<RiskControlsPayload> {
  const response = await fetch(`/api/risk-controls?ts=${Date.now()}`);

  if (!response.ok) {
    throw new Error(`Failed to load risk controls: ${response.status}`);
  }

  return response.json() as Promise<RiskControlsPayload>;
}

function RiskControlsPanel() {
  const [payload, setPayload] = useState<RiskControlsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const loadControls = () => {
      getRiskControls()
        .then((nextPayload) => {
          if (!isMounted) {
            return;
          }

          setPayload(nextPayload);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!isMounted) {
            return;
          }

          setError(err instanceof Error ? err.message : 'Failed to load risk controls.');
        });
    };

    loadControls();
    const intervalId = window.setInterval(loadControls, RISK_CONTROLS_POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

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
            <p className="risk-control-card__source">Source: {control.source}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export default RiskControlsPanel;
