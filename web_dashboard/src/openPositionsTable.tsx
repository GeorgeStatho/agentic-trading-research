import { fetchPolledJson } from './api';
import { formatCurrency, formatSignedPercent, formatToken, hasNumericValue } from './format';
import { formatOccSymbol } from './occ';
import { DataTable } from './ui/DataTable';
import type { Column } from './ui/DataTable';
import { Panel } from './ui/Panel';
import { usePolledResource } from './useRemoteResource';

export type OpenPosition = {
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

function getOpenPositions(): Promise<OpenPositionsPayload> {
  return fetchPolledJson<OpenPositionsPayload>('/api/open-positions', 'open positions');
}

function PnlPercent({ value }: { value: number | null }) {
  if (!hasNumericValue(value)) {
    return <span className="text-dim">—</span>;
  }
  let tone = '';
  if (value > 0) {
    tone = 'text-gain';
  } else if (value < 0) {
    tone = 'text-loss';
  }
  return <span className={tone}>{formatSignedPercent(value)}</span>;
}

function exitFloor(position: OpenPosition): string {
  const protection = position.profit_protection;
  if (!protection || protection.floor_type === 'unavailable') {
    return '—';
  }
  const kind = protection.floor_type === 'trailing_profit' ? 'Profit floor' : 'Stop';
  return `${kind} ${formatSignedPercent(protection.floor_profit_pct, { placeholder: '' })}`.trim();
}

const COLUMNS: readonly Column<OpenPosition>[] = [
  { key: 'contract', header: 'Contract', render: (position) => <span className="font-mono whitespace-nowrap">{formatOccSymbol(position.contract_symbol)}</span> },
  { key: 'qty', header: 'Qty', align: 'right', render: (position) => position.quantity ?? '—' },
  { key: 'entry', header: 'Entry', align: 'right', render: (position) => formatCurrency(position.entry_price, { placeholder: '—' }) },
  { key: 'mark', header: 'Mark', align: 'right', render: (position) => formatCurrency(position.mark_price, { placeholder: '—' }) },
  { key: 'pnl', header: 'P/L', align: 'right', render: (position) => <PnlPercent value={position.unrealized_pl_pct} /> },
  { key: 'dte', header: 'DTE', align: 'right', render: (position) => position.days_to_expiration ?? '—' },
  { key: 'floor', header: 'Exit floor', render: exitFloor },
  { key: 'thesis', header: 'Thesis', render: (position) => formatToken(position.thesis_verdict?.thesis_state, { placeholder: '—' }) },
];

/** What the account holds right now, one row per position. */
export function HoldingsView({ positions }: { positions: readonly OpenPosition[] }) {
  return (
    <Panel title="Holdings" meta={positions.length ? `${positions.length} open` : undefined} flush>
      {positions.length === 0 ? <p className="p-3.5 text-ink-2">No open positions.</p> : (
        <DataTable columns={COLUMNS} rows={positions} rowKey={(position) => position.contract_symbol} />
      )}
    </Panel>
  );
}

function OpenPositionsTable() {
  const { payload, error } = usePolledResource(getOpenPositions, { errorMessage: 'Failed to load open positions.' });

  if (!payload) {
    return <Panel title="Holdings"><p className={error ? 'text-error' : 'text-dim'}>{error ?? 'Loading positions…'}</p></Panel>;
  }
  return <HoldingsView positions={payload.positions} />;
}

export default OpenPositionsTable;
