import { fetchPolledJson } from './api';
import { formatText } from './format';
import { usePolledResource } from './useRemoteResource';
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

function getWhyBotTraded(): Promise<WhyBotTradedPayload> {
  return fetchPolledJson<WhyBotTradedPayload>('/api/why-bot-traded', 'trade explanation');
}

function WhyBotTradedPanel() {
  const { payload, error } = usePolledResource(getWhyBotTraded, {
    errorMessage: 'Failed to load bot trade explanation.',
  });

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
            <p className="why-bot-card__value">{formatText(payload.ticker)}</p>
          </article>
          <article className="why-bot-card">
            <p className="why-bot-card__label">Decision</p>
            <p className="why-bot-card__value">{formatText(payload.decision)}</p>
          </article>
          <article className="why-bot-card">
            <p className="why-bot-card__label">Confidence</p>
            <p className="why-bot-card__value">{formatText(payload.confidence)}</p>
          </article>
          <article className="why-bot-card why-bot-card--wide">
            <p className="why-bot-card__label">Reason</p>
            <p className="why-bot-card__value why-bot-card__value--body">{formatText(payload.reason)}</p>
          </article>
          <article className="why-bot-card">
            <p className="why-bot-card__label">Selected Contract</p>
            <p className="why-bot-card__value">{formatText(payload.selected_contract)}</p>
          </article>
          <article className="why-bot-card">
            <p className="why-bot-card__label">Rejected Because</p>
            <p className="why-bot-card__value">{formatText(payload.rejected_because)}</p>
          </article>
        </div>
      )}
    </section>
  );
}

export default WhyBotTradedPanel;
