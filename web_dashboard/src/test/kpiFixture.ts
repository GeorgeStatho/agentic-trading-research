import type { DashboardKpiPayload } from '../dashboardKpisApi.ts'

/** A `/api/dashboard-kpis` payload shaped like the live one; override what a test cares about. */
export function kpiPayload(overrides: Partial<DashboardKpiPayload> = {}): DashboardKpiPayload {
  return {
    account_equity: 2379.77,
    buying_power: 2379.77,
    day_pl: -225.02,
    day_pl_pct: -8.64,
    open_positions: 0,
    options_exposure: { market_value: 0, equity_pct: 0, position_count: 0 },
    option_exit_rules: {
      take_profit_summary: '12-20 DTE: +35% / 20-30 DTE: +55%',
      stop_loss_summary: '12-20 DTE: -25% / 20-30 DTE: -30%',
      fallback_take_profit_pct: 45,
      fallback_stop_loss_pct: -35,
    },
    win_rate: { wins: 0, closed_trades: 2, win_rate_pct: 0 },
    max_drawdown_pct: 19.84,
    performance_summary: {
      total_return_pct: -13.17,
      annualized_sharpe: -4,
      annualized_sortino: -4.7,
      profit_factor: 0,
      average_trade_return_pct: -24.24,
    },
    usage_summary: { unique_install_count: 2, startup_ping_count: 32, last_seen_at: '2026-11-03T20:00:00' },
    bot_status: { state: 'healthy', label: 'Healthy', detail: '' },
    market_status: { state: 'closed', label: 'Closed', detail: '' },
    market_regime: { available: true, state: 'high_volatility' },
    thesis_validator: {
      available: true,
      mode: 'shadow',
      flip_rate: 0.12,
      action_rate: 0,
      grounding_rate: 1,
      evaluated_count: 17,
      trading_day_count: 17,
    },
    top_rankings: {
      top_sectors: [
        {
          sector_key: 'technology',
          label: 'Technology',
          score: 74,
          top_industries: [{ industry_key: 'semiconductors', label: 'Semiconductors', score: 35 }],
        },
      ],
    },
    ...overrides,
  }
}
