import { fetchJson } from './api'

/** Read-only reports the bot already computes for its CLI tools, served by api_routes/reports.py. */

export type ExecutionQualityRow = {
  paper: number
  order_side: string | null
  submitted_orders: number
  observed_orders: number
  unobserved_orders: number
  orders_with_fills: number
  full_fill_rate: number | null
  any_fill_rate: number | null
  fill_price_samples: number
  slippage_samples: number
  mean_slippage_vs_mid: number | null
  mean_slippage_vs_arrival: number | null
  mean_spread_paid_pct: number | null
  measured_slippage_dollars: number | null
  decision_premium_dollars: number | null
  counterfactual_samples: number
  missed_trade_opportunity_pnl: number | null
}

export type ExecutionQualityPayload = { days: number; rows: ExecutionQualityRow[] }

export function getExecutionQuality(): Promise<ExecutionQualityPayload> {
  return fetchJson('/api/execution-quality?days=30', 'execution quality')
}
