import { CsvLink } from './ui/CsvLink';
import { getExecutedTrades } from './executedTradesApi';
import type { ExecutedTrade } from './executedTradesApi';
import { formatCurrency, formatShortDateTime, formatSignedPercent, formatToken, hasNumericValue } from './format';
import { formatOccSymbol } from './occ';
import { groupOrders, orderTypeLabel, wasNotExecuted } from './orders';
import type { OrderGroup } from './orders';
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

function isTrade(group: OrderGroup): boolean {
  return group.orders.length > 1;
}

function lead(group: OrderGroup): ExecutedTrade {
  return group.entry ?? group.orders[group.orders.length - 1];
}

function fillText(group: OrderGroup): string {
  const fill = (order: ExecutedTrade | null) => formatCurrency(order?.fill_price, { placeholder: '—' });
  if (!isTrade(group)) return fill(group.orders[0]);
  return group.exit ? `${fill(group.entry)} → ${fill(group.exit)}` : fill(group.entry);
}

const SUBMITTED: Column<OrderGroup> = {
  key: 'submitted',
  header: 'Submitted',
  render: (group) => <span className="font-mono whitespace-nowrap text-dim">{formatShortDateTime(lead(group).submitted_at)}</span>,
};
const CONTRACT: Column<OrderGroup> = {
  key: 'contract',
  header: 'Contract',
  render: (group) => <span className="font-mono whitespace-nowrap">{formatOccSymbol(group.orders[0].option_symbol)}</span>,
};
const TYPE: Column<OrderGroup> = {
  key: 'type',
  header: 'Type',
  render: (group) => {
    if (!isTrade(group)) return <span className="whitespace-nowrap">{orderTypeLabel(group.orders[0])}</span>;
    return <>
      <span className="whitespace-nowrap">{orderTypeLabel(lead(group))}{group.exit ? ` → ${orderTypeLabel(group.exit)}` : ' · open'}</span>
      {group.canceled ? <span className="block text-[11px] text-dim">{group.canceled} canceled attempt{group.canceled === 1 ? '' : 's'}</span> : null}
    </>;
  },
};
const FILL: Column<OrderGroup> = { key: 'fill', header: 'Fill', align: 'right', render: (group) => <span className="font-mono whitespace-nowrap">{fillText(group)}</span> };
const RETURN: Column<OrderGroup> = {
  key: 'return',
  header: 'P/L',
  align: 'right',
  render: (group) => {
    if (group.returnPct === null) return <span className="text-dim">—</span>;
    return <span className={`font-mono ${group.returnPct < 0 ? 'text-loss' : 'text-gain'}`}>{formatSignedPercent(group.returnPct)}</span>;
  },
};
const STATUS: Column<OrderGroup> = { key: 'status', header: 'Status', render: (group) => <StatusTag status={(group.exit ?? group.entry ?? group.orders[0]).order_status} /> };

/** Every option order, newest first; an entry and its exit share one row once the API links them. */
export function OrdersView({ trades }: { trades: readonly ExecutedTrade[] }) {
  const groups = groupOrders(trades);
  const hasFills = trades.some((trade) => hasNumericValue(trade.fill_price));
  const hasReturns = groups.some((group) => group.returnPct !== null);
  const columns = [SUBMITTED, CONTRACT, TYPE, ...(hasFills ? [FILL] : []), ...(hasReturns ? [RETURN] : []), STATUS];
  const meta = groups.length === trades.length
    ? `${trades.length} recorded`
    : `${groups.length} trade${groups.length === 1 ? '' : 's'} · ${trades.length} orders`;

  return (
    <Panel title="Orders" meta={meta} actions={<CsvLink href="/api/executed-trades?limit=500&format=csv" />} flush>
      <DataTable
        columns={columns}
        rows={groups}
        rowKey={(group) => group.key}
        rowClassName={(group) => (group.orders.every((order) => wasNotExecuted(order.order_status)) ? 'opacity-60' : '')}
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
