import { fetchPolledJson } from './api';
import { asUtcTimestamp, formatToken } from './format';

/**
 * The equity, drawdown and daily P/L series behind the Live charts, from
 * `/api/portfolio-history`. Each timestamp is one trading session, stamped
 * in the evening ET of that session's day.
 */

export type PortfolioHistory = {
  timestamp: number[];
  equity: number[];
  profit_loss: number[];
};

export type EquityPoint = {
  /** Epoch milliseconds. */
  time: number;
  equity: number;
  /** The session's P/L as the broker reports it, so deposits do not count as gains. */
  pnl: number;
};

export type ChartRange = '1W' | '1M' | '3M' | 'All';

const DAY_MS = 86_400_000;
const RANGE_DAYS: Record<Exclude<ChartRange, 'All'>, number> = { '1W': 7, '1M': 31, '3M': 92 };

export function getPortfolioHistory(): Promise<PortfolioHistory> {
  return fetchPolledJson<PortfolioHistory>('/api/portfolio-history', 'portfolio history');
}

/** Sessions from the first funded one on; before it the account was empty and the line sat at $0. */
export function equityPoints(history: PortfolioHistory): EquityPoint[] {
  const firstFunded = history.equity.findIndex((equity) => equity > 0);
  if (firstFunded === -1) {
    return [];
  }
  return history.timestamp.slice(firstFunded).map((seconds, offset) => ({
    time: seconds * 1000,
    equity: history.equity[firstFunded + offset],
    pnl: history.profit_loss[firstFunded + offset] ?? 0,
  }));
}

export function pointsInRange(points: readonly EquityPoint[], range: ChartRange): EquityPoint[] {
  if (range === 'All' || points.length === 0) {
    return [...points];
  }
  const start = points[points.length - 1].time - RANGE_DAYS[range] * DAY_MS;
  return points.filter((point) => point.time >= start);
}

/** Each point's distance below the highest equity before it, in percent (0 or negative). */
export function drawdownPct(points: readonly EquityPoint[]): number[] {
  let peak = -Infinity;
  return points.map((point) => {
    peak = Math.max(peak, point.equity);
    return peak > 0 ? Math.round((point.equity / peak - 1) * 10_000) / 100 : 0;
  });
}

/** The trading day (ET) a timestamp falls on, as YYYY-MM-DD. */
const marketDay = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' });

export type MarkerTrade = {
  underlying_symbol: string;
  decision: string;
  order_side: string | null;
  order_status: string | null;
  submitted_at: string | null;
};

export type TradeMarker = {
  time: number;
  equity: number;
  kind: 'entry' | 'exit';
  label: string;
};

const NOT_EXECUTED = new Set(['canceled', 'cancelled', 'rejected', 'expired']);

const CONTRACT_LETTER: Readonly<Record<string, string>> = { call: ' C', put: ' P' };

function entryLabel(trade: MarkerTrade): string {
  return `${trade.underlying_symbol}${CONTRACT_LETTER[trade.decision.toLowerCase()] ?? ''}`;
}

/** Entry and exit markers on the session each order was placed. */
export function tradeMarkers(points: readonly EquityPoint[], trades: readonly MarkerTrade[]): TradeMarker[] {
  const pointByDay = new Map(points.map((point) => [marketDay.format(point.time), point]));

  return trades.flatMap((trade) => {
    if (!trade.submitted_at || NOT_EXECUTED.has(String(trade.order_status).toLowerCase())) {
      return [];
    }
    const point = pointByDay.get(marketDay.format(new Date(asUtcTimestamp(trade.submitted_at))));
    if (!point) {
      return [];
    }
    const isExit = trade.order_side === 'sell';
    return [{
      time: point.time,
      equity: point.equity,
      kind: isExit ? 'exit' : 'entry',
      label: isExit ? `${trade.underlying_symbol} ${formatToken(trade.decision).toLowerCase()}` : entryLabel(trade),
    } satisfies TradeMarker];
  });
}
