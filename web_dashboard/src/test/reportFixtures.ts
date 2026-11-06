import type { ExecutionQualityPayload } from '../reportsApi.ts'

/** Shaped like `/api/execution-quality?days=30`: paper buys, half of them filled in full. */
export function executionQualityPayload(): ExecutionQualityPayload {
  return {
    days: 30,
    rows: [{
      paper: 1, order_side: 'OrderSide.BUY', submitted_orders: 4, observed_orders: 4, unobserved_orders: 0, orders_with_fills: 3,
      full_fill_rate: 0.5, any_fill_rate: 0.75, fill_price_samples: 3, slippage_samples: 3,
      mean_slippage_vs_mid: 0.04, mean_slippage_vs_arrival: -0.01, mean_spread_paid_pct: 0.062,
      measured_slippage_dollars: 12, decision_premium_dollars: 600, counterfactual_samples: 0,
      missed_trade_opportunity_pnl: null,
    }],
  }
}
