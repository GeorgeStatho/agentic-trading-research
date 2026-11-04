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
