import { fetchPolledJson } from './api';
import { useFetchedResource } from './useRemoteResource';
import './card.css';

type ExecutedTrade = {
  id: number;
  order_id: string;
  underlying_symbol: string;
  company_name: string | null;
  option_symbol: string;
  decision: string;
  expiration_date: string | null;
  strike_price: number | null;
  order_status: string | null;
  submitted_at: string | null;
  error: string | null;
};

type ExecutedTradesResponse = {
  trades: ExecutedTrade[];
};

function getExecutedTrades(): Promise<ExecutedTradesResponse> {
  return fetchPolledJson<ExecutedTradesResponse>('/api/executed-trades', 'executed trades');
}

function getExecutedTradeRows(payload: ExecutedTradesResponse): ExecutedTrade[] {
  return Array.isArray(payload.trades) ? payload.trades : [];
}

function executedTradeToText(trade: ExecutedTrade): string {
  const normalizedDecision = trade.decision.toUpperCase();
  const companyName = trade.company_name || 'Unknown company';
  const strikeText = trade.strike_price == null ? 'unknown strike' : `strike ${trade.strike_price}`;
  const expirationText = trade.expiration_date || 'unknown expiration';
  const statusText = trade.order_status || 'unknown';
  const submittedText = trade.submitted_at ? ` Submitted at ${trade.submitted_at}.` : '';

  return `Option order ${normalizedDecision} for ${trade.underlying_symbol} (${companyName}) used contract ${trade.option_symbol} expiring on ${expirationText} at ${strikeText} with status ${statusText}.${submittedText}`;
}

function OrderCard({ trade }: { trade: ExecutedTrade }) {
  return (
    <article className="order-card">
      <div className="order-card__header">
        <div>
          <p className="order-card__eyebrow">{trade.company_name || 'Executed option order'}</p>
          <h3 className="order-card__title">{trade.underlying_symbol} {trade.decision.toUpperCase()}</h3>
        </div>
        <span className="order-card__status">{trade.order_status || 'Unknown status'}</span>
      </div>
      <p className="order-card__text">{executedTradeToText(trade)}</p>
      {trade.error ? (
        <p className="order-card__error">{trade.error}</p>
      ) : null}
    </article>
  );
}

function OrderCardList() {
  const { payload, error, isLoading } = useFetchedResource(getExecutedTrades, {
    errorMessage: 'Failed to load executed trades.',
  });
  const trades = payload ? getExecutedTradeRows(payload) : [];

  if (isLoading) {
    return <section className="order-card-list order-card-list--empty"><p>Loading executed trades…</p></section>;
  }

  if (error) {
    return <section className="order-card-list order-card-list--empty"><p>{error}</p></section>;
  }

  if (trades.length === 0) {
    return (
      <section className="order-card-list order-card-list--empty">
        <div className="order-card-list__header"><div><p>Executed Orders</p><h2>Recent execution history</h2></div></div>
        <p>No executed trades have been recorded yet.</p>
      </section>
    );
  }

  return (
    <section className="order-card-list">
      <div className="order-card-list__header">
        <div><p>Executed Orders</p><h2>Recent execution history</h2></div>
        <span>{trades.length} recorded</span>
      </div>
      {trades.map((trade) => (
        <OrderCard
          key={`${trade.id}-${trade.order_id}`}
          trade={trade}
        />
      ))}
    </section>
  );
}

export default OrderCardList;
