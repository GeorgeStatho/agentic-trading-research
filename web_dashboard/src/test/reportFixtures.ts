import type { DecisionAttributionPayload, ExecutionQualityPayload, ExitAttributionPayload, MacroCalendarPayload, MacroEvent } from '../reportsApi.ts'

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

/** Shaped like `/api/exit-attribution` on Nov 6, 2026: the two exit reasons that closed positions. */
export function exitAttributionPayload(): ExitAttributionPayload {
  return {
    rows: [
      { exit_reason: 'trailing_profit_stop', closed_positions: 10, observed_path_count: 6, median_mfe_minus_realized_pct: 27.06 },
      { exit_reason: 'stop_loss', closed_positions: 10, observed_path_count: 2, median_mfe_minus_realized_pct: 43.69 },
    ],
  }
}

function group(key: string, count: number, graded: number, rate: number | null, se: number | null) {
  return { key, count, graded, hit_rate: rate, hit_rate_se: se }
}

/** Shaped like `/api/decision-attribution?days=90&horizon=5` on Nov 6, 2026, trimmed to a few groups. */
export function decisionAttributionPayload(): DecisionAttributionPayload {
  return {
    days: 90,
    horizon: 5,
    total: 2048,
    by_quality: [group('adequate', 1334, 498, 0.494, 0.023), group('weak', 1317, 345, 0.464, 0.027)],
    by_source: [group('grounding_proxy', 900, 400, 0.51, 0.025)],
    by_self_rating: [group('high', 700, 300, 0.55, 0.03), group('low', 200, 80, 0.41, 0.055)],
    primary_failures: [{ key: 'none', count: 1200 }, { key: 'thin_evidence', count: 600 }],
  }
}

function macroEvent(name: string, at: string, importance: string, currency = 'USD', country = 'United States'): MacroEvent {
  return { event_name: name, scheduled_at_utc: at, country, currency, importance, forecast: '0.3%', previous: '0.2%' }
}

/** Shaped like `/api/macro-calendar?days=7` early in a week: the calendar runs to Friday. */
export function macroCalendarPayload(): MacroCalendarPayload {
  return {
    days: 7,
    calendar_through: '2026-11-06T21:45:00+00:00',
    events: [
      macroEvent('Goods Trade Balance', '2026-11-03T00:30:00+00:00', 'low', 'AUD', 'Australia'),
      macroEvent('ISM Services PMI', '2026-11-03T15:00:00+00:00', 'high'),
      macroEvent('German Factory Orders m/m', '2026-11-04T07:00:00+00:00', 'medium', 'EUR', 'Euro Area'),
      macroEvent('Unemployment Claims', '2026-11-05T13:30:00+00:00', 'medium'),
      macroEvent('Non-Farm Employment Change', '2026-11-06T13:30:00+00:00', 'high'),
    ],
  }
}
