import { getExecutedTrades } from './executedTradesApi';
import type { ExecutedTrade } from './executedTradesApi';
import { formatCurrency, formatShortDateTime, formatToken, hasNumericValue } from './format';
import { formatOccSymbol } from './occ';
import { orderTypeLabel, wasNotExecuted } from './orders';
import { DataTable } from './ui/DataTable';
import type { Column } from './ui/DataTable';
import { Panel } from './ui/Panel';
import { Tag } from './ui/Tag';
import { useFetchedResource } from './useRemoteResource';

function StatusTag({ status }: { status: string | null }) {
  const token = formatToken(status, { placeholder: 'Unknown' });
  const label = token.charAt(0).toUpperCase() + token.slice(1);
  return status === 'filled' ? <Tag tone="gain">{label}</Tag> : <Tag>{label}</Tag>;
}

const SUBMITTED: Column<ExecutedTrade> = {
  key: 'submitted',
  header: 'Submitted',
  render: (trade) => <span className="font-mono whitespace-nowrap text-dim">{formatShortDateTime(trade.submitted_at)}</span>,
};
const CONTRACT: Column<ExecutedTrade> = {
  key: 'contract',
  header: 'Contract',
  render: (trade) => <span className="font-mono whitespace-nowrap">{formatOccSymbol(trade.option_symbol)}</span>,
};
const TYPE: Column<ExecutedTrade> = { key: 'type', header: 'Type', render: (trade) => <span className="whitespace-nowrap">{orderTypeLabel(trade)}</span> };
const FILL: Column<ExecutedTrade> = {
  key: 'fill',
  header: 'Fill',
  align: 'right',
  render: (trade) => <span className="font-mono">{formatCurrency(trade.fill_price, { placeholder: '—' })}</span>,
};
const STATUS: Column<ExecutedTrade> = { key: 'status', header: 'Status', render: (trade) => <StatusTag status={trade.order_status} /> };

/** Every option order, newest first as the API returns them; canceled ones dimmed. */
export function OrdersView({ trades }: { trades: readonly ExecutedTrade[] }) {
  const hasFills = trades.some((trade) => hasNumericValue(trade.fill_price));
  const columns = hasFills ? [SUBMITTED, CONTRACT, TYPE, FILL, STATUS] : [SUBMITTED, CONTRACT, TYPE, STATUS];

  return (
    <Panel title="Orders" meta={`${trades.length} recorded`} flush>
      <DataTable
        columns={columns}
        rows={trades}
        rowKey={(trade) => String(trade.id)}
        rowClassName={(trade) => (wasNotExecuted(trade.order_status) ? 'opacity-60' : '')}
        emptyMessage="No orders yet."
      />
    </Panel>
  );
}

function OrderCardList() {
  const { payload, error } = useFetchedResource(getExecutedTrades, { errorMessage: 'Failed to load executed trades.' });

  if (!payload) {
    return <Panel title="Orders"><p className={error ? 'text-error' : 'text-dim'}>{error ?? 'Loading orders…'}</p></Panel>;
  }
  return <OrdersView trades={Array.isArray(payload.trades) ? payload.trades : []} />;
}

export default OrderCardList;
