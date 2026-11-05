export type LagTrade = {
  symbol: string;
  lag_hours: number | null;
  option_pnl_pct: number | null;
};

export type LagPoint = { symbol: string; lag: number; pnl: number };

/** Entries with both a measured news-to-order lag and a closed option P/L. */
export function lagPoints(trades: readonly LagTrade[]): LagPoint[] {
  return trades.flatMap((trade) =>
    trade.lag_hours === null || trade.option_pnl_pct === null
      ? []
      : [{ symbol: trade.symbol, lag: trade.lag_hours, pnl: trade.option_pnl_pct }],
  );
}
