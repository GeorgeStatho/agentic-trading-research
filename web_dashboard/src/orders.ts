import type { ExecutedTrade } from './executedTradesApi';
import { formatToken } from './format';

/** "Entry · buy put" for an opening order, "Trailing profit stop · sell" for an exit. */
export function orderTypeLabel(trade: Pick<ExecutedTrade, 'order_side' | 'decision'>): string {
  if (trade.order_side === 'sell') {
    return `${formatToken(trade.decision)} · sell`;
  }
  return `Entry · buy ${formatToken(trade.decision).toLowerCase()}`;
}

const NOT_EXECUTED = new Set(['canceled', 'cancelled', 'rejected', 'expired']);

/** True for orders that never traded: canceled, rejected or expired. */
export function wasNotExecuted(status: string | null): boolean {
  return NOT_EXECUTED.has(String(status).toLowerCase());
}

/** One row of the Orders table: a lone order, or an entry with its exit and exit attempts. */
export type OrderGroup = {
  key: string;
  /** Newest first, as the API returns them. */
  orders: ExecutedTrade[];
  entry: ExecutedTrade | null;
  exit: ExecutedTrade | null;
  canceled: number;
  /** Exit fill against entry fill, in percent; null until both are known. */
  returnPct: number | null;
};

function isFilled(order: ExecutedTrade): boolean {
  return String(order.order_status).toLowerCase() === 'filled';
}

/** Orders folded by `position_id`, ordered by each trade's newest order. */
export function groupOrders(trades: readonly ExecutedTrade[]): OrderGroup[] {
  const groups = new Map<string, ExecutedTrade[]>();
  for (const order of trades) {
    const key = order.position_id ?? `order:${order.id}`;
    groups.set(key, [...(groups.get(key) ?? []), order]);
  }
  return Array.from(groups, ([key, orders]) => {
    const entry = orders.find((order) => order.order_side === 'buy' && isFilled(order)) ?? null;
    const exit = orders.find((order) => order.order_side === 'sell' && isFilled(order)) ?? null;
    const entryFill = entry?.fill_price;
    const exitFill = exit?.fill_price;
    const returnPct = typeof entryFill === 'number' && typeof exitFill === 'number' && entryFill > 0 ? ((exitFill - entryFill) / entryFill) * 100 : null;
    return { key, orders, entry, exit, canceled: orders.filter((order) => wasNotExecuted(order.order_status)).length, returnPct };
  });
}
