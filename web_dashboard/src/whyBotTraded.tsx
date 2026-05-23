import { useEffect, useState } from 'react';
import './whyBotTraded.css';

type WhyBotTradedPayload = {
  ran_at: string;
  has_decision: boolean;
  ticker: string;
  decision: string;
  confidence: string;
  reason: string;
  selected_contract: string;
  rejected_because: string;
  submitted: boolean;
};

const WHY_BOT_TRADED_POLL_INTERVAL_MS = 60_000;

async function getWhyBotTraded(): Promise<WhyBotTradedPayload> {
  const response = await fetch(`/api/why-bot-traded?ts=${Date.now()}`);

  if (!response.ok) {
    throw new Error(`Failed to load trade explanation: ${response.status}`);
  }

  return response.json() as Promise<WhyBotTradedPayload>;
}

function formatLabel(value: string): string {
  const normalized = value.trim();
  if (!normalized) {
    return 'N/A';
  }

  return normalized;
}

function WhyBotTradedPanel() {
  const [payload, setPayload] = useState<WhyBotTradedPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const loadExplanation = () => {
      getWhyBotTraded()
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

          setError(err instanceof Error ? err.message : 'Failed to load bot trade explanation.');
        });
    };

    loadExplanation();
    const intervalId = window.setInterval(loadExplanation, WHY_BOT_TRADED_POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

  if (error) {
    return (
      <section className="why-bot-panel">
        <div className="why-bot-panel__header">
          <div>
            <p className="why-bot-panel__eyebrow">Why Did The Bot Trade?</p>
            <h2>Decision context unavailable</h2>
          </div>
        </div>
        <p className="why-bot-panel__empty">{error}</p>
      </section>
    );
  }

  if (!payload) {
    return (
      <section className="why-bot-panel">
        <div className="why-bot-panel__header">
          <div>
            <p className="why-bot-panel__eyebrow">Why Did The Bot Trade?</p>
            <h2>Loading decision context</h2>
          </div>
        </div>
        <p className="why-bot-panel__empty">Reading the latest strategist and manager outputs.</p>
      </section>
    );
  }

  return (
    <section className="why-bot-panel">
      <div className="why-bot-panel__header">
        <div>
          <p className="why-bot-panel__eyebrow">Why Did The Bot Trade?</p>
          <h2>Latest AI decision</h2>
        </div>
        <span className={`why-bot-panel__pill${payload.submitted ? ' why-bot-panel__pill--submitted' : ''}`}>
          {payload.submitted ? 'Order submitted' : 'Not submitted'}
        </span>
      </div>

      {!payload.has_decision ? (
        <p className="why-bot-panel__empty">{payload.reason}</p>
      ) : (
        <div className="why-bot-grid">
          <article className="why-bot-card">
            <p className="why-bot-card__label">Ticker</p>
            <p className="why-bot-card__value">{formatLabel(payload.ticker)}</p>
          </article>
          <article className="why-bot-card">
            <p className="why-bot-card__label">Decision</p>
            <p className="why-bot-card__value">{formatLabel(payload.decision)}</p>
          </article>
          <article className="why-bot-card">
            <p className="why-bot-card__label">Confidence</p>
            <p className="why-bot-card__value">{formatLabel(payload.confidence)}</p>
          </article>
          <article className="why-bot-card why-bot-card--wide">
            <p className="why-bot-card__label">Reason</p>
            <p className="why-bot-card__value why-bot-card__value--body">{formatLabel(payload.reason)}</p>
          </article>
          <article className="why-bot-card">
            <p className="why-bot-card__label">Selected Contract</p>
            <p className="why-bot-card__value">{formatLabel(payload.selected_contract)}</p>
          </article>
          <article className="why-bot-card">
            <p className="why-bot-card__label">Rejected Because</p>
            <p className="why-bot-card__value">{formatLabel(payload.rejected_because || 'N/A')}</p>
          </article>
        </div>
      )}
    </section>
  );
}

export default WhyBotTradedPanel;
