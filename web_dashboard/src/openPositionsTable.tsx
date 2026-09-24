import { fetchPolledJson } from './api';
import {
  MISSING_VALUE_PLACEHOLDER,
  formatCurrency,
  formatNumber,
  formatPercent,
  formatSignedPercent,
  formatToken,
} from './format';
import { usePolledResource } from './useRemoteResource';
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
  mark_price: number | null;
  mid_price: number | null;
  unrealized_pl_pct: number | null;
  days_to_expiration: number | null;
  profit_protection: {
    floor_type: 'trailing_profit' | 'stop_loss' | 'unavailable';
    floor_profit_pct: number | null;
    floor_mark_price: number | null;
    trailing_profit_armed: boolean;
    high_water_profit_pct: number | null;
    high_water_mark_price: number | null;
  } | null;
  exit_rule_status: string;
  decision_reasons: string[];
  thesis_verdict: {
    thesis_state: string;
    would_open_today: string | null;
    rationale: string | null;
    consistency: string | null;
    arbiter_action: string | null;
    arbiter_reason: string | null;
    run_date: string | null;
    mode: string | null;
    executed: boolean;
    grounded: boolean;
  } | null;
};

type OpenPositionsPayload = {
  position_count: number;
  option_count: number;
  stock_count: number;
  positions: OpenPosition[];
};

const POSITION_PERCENT_DIGITS = 1;

function getOpenPositions(): Promise<OpenPositionsPayload> {
  return fetchPolledJson<OpenPositionsPayload>('/api/open-positions', 'open positions');
}

function formatPositionPercent(value: number | null): string {
  return formatPercent(value, { digits: POSITION_PERCENT_DIGITS });
}

function formatProfitLossPercent(value: number | null): string {
  return formatSignedPercent(value, { digits: POSITION_PERCENT_DIGITS, suffix: ' P/L' });
}

function formatBidAsk(position: OpenPosition): string {
  if (position.current_bid === null && position.current_ask === null) {
    return MISSING_VALUE_PLACEHOLDER;
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

function getThesisTone(state: string): string {
  switch (state) {
    case 'intact':
      return 'positive';
    case 'degraded':
      return 'warning';
    case 'invalidated':
      return 'negative';
    default:
      return 'neutral';
  }
}


function buildThesisTooltip(verdict: NonNullable<OpenPosition['thesis_verdict']>): string {
  const parts = [
    verdict.rationale || '',
    verdict.would_open_today ? `Would open today: ${verdict.would_open_today}` : '',
    verdict.consistency ? `Samples: ${verdict.consistency}` : '',
    verdict.run_date ? `Checked ${verdict.run_date} (${verdict.mode ?? 'shadow'} mode)` : '',
  ];
  return parts.filter(Boolean).join('\n');
}

function getFloorLabel(position: OpenPosition): string {
  if (position.profit_protection?.floor_type === 'trailing_profit') {
    return 'Profit floor';
  }
  if (position.profit_protection?.floor_type === 'stop_loss') {
    return 'Stop-loss floor';
  }
  return 'Floor unavailable';
}

function OpenPositionsTable() {
  const { payload, error } = usePolledResource(getOpenPositions, {
    errorMessage: 'Failed to load open positions.',
  });

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
                <th>Current Mark</th>
                <th>Mid Price</th>
                <th>P/L %</th>
                <th>Manager Sell Floor</th>
                <th>Highest Recorded</th>
                <th>Days to Expiration</th>
                <th>Exit Rule Status</th>
                <th>Thesis</th>
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
                  <td>{formatNumber(position.strike)}</td>
                  <td>{position.expiration || MISSING_VALUE_PLACEHOLDER}</td>
                  <td>{formatNumber(position.quantity)}</td>
                  <td>{formatCurrency(position.entry_price)}</td>
                  <td>{formatBidAsk(position)}</td>
                  <td>{formatCurrency(position.mark_price)}</td>
                  <td>{formatCurrency(position.mid_price)}</td>
                  <td className={position.unrealized_pl_pct !== null && position.unrealized_pl_pct < 0 ? 'positions-table__negative' : position.unrealized_pl_pct !== null && position.unrealized_pl_pct > 0 ? 'positions-table__positive' : ''}>
                    {formatPositionPercent(position.unrealized_pl_pct)}
                  </td>
                  <td>
                    <div className={`positions-table__protection${position.profit_protection?.trailing_profit_armed ? ' positions-table__protection--armed' : ''}`}>
                      <span className="positions-table__protection-label">{getFloorLabel(position)}</span>
                      <strong>{formatCurrency(position.profit_protection?.floor_mark_price ?? null)}</strong>
                      <span>{formatProfitLossPercent(position.profit_protection?.floor_profit_pct ?? null)}</span>
                    </div>
                  </td>
                  <td>
                    <div className="positions-table__protection">
                      <span className="positions-table__protection-label">High-water mark</span>
                      <strong>{formatCurrency(position.profit_protection?.high_water_mark_price ?? null)}</strong>
                      <span>{formatProfitLossPercent(position.profit_protection?.high_water_profit_pct ?? null)}</span>
                    </div>
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
                  <td>
                    {position.thesis_verdict ? (
                      <>
                        <span
                          className={`positions-table__badge positions-table__badge--${getThesisTone(position.thesis_verdict.thesis_state)}`}
                          title={buildThesisTooltip(position.thesis_verdict)}
                        >
                          {formatToken(position.thesis_verdict.thesis_state)}
                        </span>
                        {position.thesis_verdict.arbiter_action && position.thesis_verdict.arbiter_action !== 'none' ? (
                          <p className="positions-table__reason">
                            {position.thesis_verdict.executed ? '' : 'shadow: '}
                            {formatToken(position.thesis_verdict.arbiter_action)}
                          </p>
                        ) : null}
                      </>
                    ) : position.position_kind === 'option' ? (
                      <span className="positions-table__badge positions-table__badge--neutral" title="No thesis captured or not validated yet.">
                        not checked
                      </span>
                    ) : (
                      MISSING_VALUE_PLACEHOLDER
                    )}
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
