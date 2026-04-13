import { useEffect, useState } from 'react';
import './card.css';

type Execution = {
  symbol: string;
  name: string;
  decision: string;
  selected_expiration_date: string;
  selected_strike_price: number;
  submitted: boolean;
  error: string | null;
};

type TradeExecutionOutput = {
  executions: Execution[];
};

async function getOrders(): Promise<TradeExecutionOutput> {
  const response = await fetch('/api/trade-execution-output');

  if (!response.ok) {
    throw new Error(`Failed to load orders: ${response.status}`);
  }

  return response.json() as Promise<TradeExecutionOutput>;
}

function getExecutions(orders: TradeExecutionOutput): Execution[] {
  return orders.executions;
}

function orderJsonToText(execution: Execution): string {
  const status = execution.submitted ? 'placed' : 'not placed';

  return `Option order ${execution.decision} for ${execution.symbol} (${execution.name}) expiring on ${execution.selected_expiration_date} at strike ${execution.selected_strike_price} was ${status}.`;
}

function OrderCard({ execution }: { execution: Execution }) {
  return (
    <article className="order-card">
      <h3 className="order-card__title">
        {execution.symbol} {execution.decision.toUpperCase()}
      </h3>
      <p className="order-card__text">{orderJsonToText(execution)}</p>
      {execution.error ? (
        <p className="order-card__error">{execution.error}</p>
      ) : null}
    </article>
  );
}

function OrderCardList() {
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;

    getOrders()
      .then((orders) => {
        if (!isMounted) {
          return;
        }

        setExecutions(getExecutions(orders));
        setError(null);
      })
      .catch((err: unknown) => {
        if (!isMounted) {
          return;
        }

        setError(err instanceof Error ? err.message : 'Failed to load orders.');
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
    return <p>Loading orders...</p>;
  }

  if (error) {
    return <p>{error}</p>;
  }

  return (
    <section className="order-card-list">
      {executions.map((execution) => (
        <OrderCard
          key={`${execution.symbol}-${execution.selected_expiration_date}-${execution.selected_strike_price}`}
          execution={execution}
        />
      ))}
    </section>
  );
}

export default OrderCardList;
