import { useEffect, useState } from 'react';
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

async function getExecutedTrades(): Promise<ExecutedTradesResponse> {
  const response = await fetch(`/api/executed-trades?ts=${Date.now()}`);

  if (!response.ok) {
    throw new Error(`Failed to load executed trades: ${response.status}`);
  }

  return response.json() as Promise<ExecutedTradesResponse>;
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
      <h3 className="order-card__title">
        {trade.underlying_symbol} {trade.decision.toUpperCase()}
      </h3>
      <p className="order-card__text">{executedTradeToText(trade)}</p>
      {trade.error ? (
        <p className="order-card__error">{trade.error}</p>
      ) : null}
    </article>
  );
}

function OrderCardList() {
  const [trades, setTrades] = useState<ExecutedTrade[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;

    getExecutedTrades()
      .then((payload) => {
        if (!isMounted) {
          return;
        }

        setTrades(getExecutedTradeRows(payload));
        setError(null);
      })
      .catch((err: unknown) => {
        if (!isMounted) {
          return;
        }

        setError(err instanceof Error ? err.message : 'Failed to load executed trades.');
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  if (isLoading) {
    return <p>Loading executed trades...</p>;
  }

  if (error) {
    return <p>{error}</p>;
  }

  if (trades.length === 0) {
    return <p>No executed trades have been recorded yet.</p>;
  }

  return (
    <section className="order-card-list">
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
