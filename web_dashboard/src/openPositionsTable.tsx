import { useEffect, useState } from 'react';
import './positions.css';

type OpenPosition = {
  symbol: string;
  contract_symbol: string;
  position_kind: 'option' | 'stock';
  type: string;
  strike: number | null;
  expiration: string;
  quantity: number | null;
  entry_price: number | null;
  current_bid: number | null;
  current_ask: number | null;
  mid_price: number | null;
  unrealized_pl_pct: number | null;
  days_to_expiration: number | null;
  exit_rule_status: string;
  decision_reasons: string[];
};

type OpenPositionsPayload = {
  position_count: number;
  option_count: number;
  stock_count: number;
  positions: OpenPosition[];
};

const POSITIONS_POLL_INTERVAL_MS = 60_000;

async function getOpenPositions(): Promise<OpenPositionsPayload> {
  const response = await fetch(`/api/open-positions?ts=${Date.now()}`);

  if (!response.ok) {
    throw new Error(`Failed to load open positions: ${response.status}`);
  }

  return response.json() as Promise<OpenPositionsPayload>;
}

function formatCurrency(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }

  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
}

function formatNumber(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }

  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}

function formatPercent(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }

  return `${value.toFixed(1)}%`;
}

function formatBidAsk(position: OpenPosition): string {
  if (position.current_bid === null && position.current_ask === null) {
    return 'N/A';
  }

  return `${formatCurrency(position.current_bid)} / ${formatCurrency(position.current_ask)}`;
}

function getExitStatusTone(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (normalized.includes('stop loss') || normalized.includes('triggered')) {
    return 'negative';
  }
  if (normalized.includes('take profit')) {
    return 'positive';
  }
  if (normalized.includes('expiration')) {
    return 'warning';
  }
  return 'neutral';
}

function OpenPositionsTable() {
  const [payload, setPayload] = useState<OpenPositionsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const loadPositions = () => {
      getOpenPositions()
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

          setError(err instanceof Error ? err.message : 'Failed to load open positions.');
        });
    };

    loadPositions();
    const intervalId = window.setInterval(loadPositions, POSITIONS_POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

  if (error) {
    return (
      <section className="positions-panel">
        <div className="positions-panel__header">
          <div>
            <p className="positions-panel__eyebrow">Open Positions</p>
            <h2>Positions table unavailable</h2>
          </div>
        </div>
        <p className="positions-panel__empty">{error}</p>
      </section>
    );
  }

  if (!payload) {
    return (
      <section className="positions-panel">
        <div className="positions-panel__header">
          <div>
            <p className="positions-panel__eyebrow">Open Positions</p>
            <h2>Loading positions</h2>
          </div>
        </div>
        <p className="positions-panel__empty">Fetching live Alpaca positions and option manager snapshots.</p>
      </section>
    );
  }

  return (
    <section className="positions-panel">
      <div className="positions-panel__header">
        <div>
          <p className="positions-panel__eyebrow">Open Positions</p>
          <h2>Live position monitor</h2>
        </div>
        <p className="positions-panel__summary">
          {payload.position_count} total, {payload.option_count} options, {payload.stock_count} stocks
        </p>
      </div>

      {payload.positions.length === 0 ? (
        <p className="positions-panel__empty">No open positions right now.</p>
      ) : (
        <div className="positions-table-shell">
          <table className="positions-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Type</th>
                <th>Strike</th>
                <th>Expiration</th>
                <th>Qty</th>
                <th>Entry</th>
                <th>Current Bid/Ask</th>
                <th>Mid Price</th>
                <th>P/L %</th>
                <th>Days to Expiration</th>
                <th>Exit Rule Status</th>
              </tr>
            </thead>
            <tbody>
              {payload.positions.map((position) => (
                <tr key={position.contract_symbol}>
                  <td>
                    <div className="positions-table__symbol-cell">
                      <span className="positions-table__symbol">{position.symbol}</span>
                      {position.position_kind === 'option' ? (
                        <span className="positions-table__contract">{position.contract_symbol}</span>
                      ) : null}
                    </div>
                  </td>
                  <td>{position.type}</td>
                  <td>{position.strike === null ? 'N/A' : formatNumber(position.strike)}</td>
                  <td>{position.expiration || 'N/A'}</td>
                  <td>{formatNumber(position.quantity)}</td>
                  <td>{formatCurrency(position.entry_price)}</td>
                  <td>{formatBidAsk(position)}</td>
                  <td>{formatCurrency(position.mid_price)}</td>
                  <td className={position.unrealized_pl_pct !== null && position.unrealized_pl_pct < 0 ? 'positions-table__negative' : position.unrealized_pl_pct !== null && position.unrealized_pl_pct > 0 ? 'positions-table__positive' : ''}>
                    {formatPercent(position.unrealized_pl_pct)}
                  </td>
                  <td>{position.days_to_expiration === null ? 'N/A' : formatNumber(position.days_to_expiration)}</td>
                  <td>
                    <span className={`positions-table__badge positions-table__badge--${getExitStatusTone(position.exit_rule_status)}`}>
                      {position.exit_rule_status}
                    </span>
                    {position.decision_reasons.length > 0 ? (
                      <p className="positions-table__reason">{position.decision_reasons[0]}</p>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default OpenPositionsTable;
