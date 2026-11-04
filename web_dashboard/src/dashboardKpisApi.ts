import { fetchPolledJson } from './api';
import type { BotComponent } from './shellStatus';

/** `/api/dashboard-kpis`: account, performance and system state for Live and System. */

export type DashboardKpiPayload = {
  account_equity: number | null;
  buying_power: number | null;
  day_pl: number | null;
  day_pl_pct: number | null;
  open_positions: number;
  options_exposure: {
    market_value: number | null;
    equity_pct: number | null;
    position_count: number;
  };
  option_exit_rules: {
    take_profit_summary: string;
    stop_loss_summary: string;
    fallback_take_profit_pct: number | null;
    fallback_stop_loss_pct: number | null;
  };
  /** Not returned yet (REDESIGN.md section 9); the stat is hidden until it is. */
  unrealized_pl?: number | null;
  win_rate: {
    wins: number;
    closed_trades: number;
    win_rate_pct: number | null;
  };
  max_drawdown_pct: number | null;
  performance_summary: {
    total_return_pct: number | null;
    annualized_sharpe: number | null;
    annualized_sortino: number | null;
    profit_factor: number | null;
    average_trade_return_pct: number | null;
  };
  usage_summary: {
    unique_install_count: number;
    startup_ping_count: number;
    last_seen_at: string;
  };
  bot_status: {
    state: string;
    label: string;
    detail: string;
    components?: BotComponent[];
  };
  market_status: {
    state: string;
    label: string;
    detail: string;
  };
  market_regime: {
    available: boolean;
    state: string;
    confidence?: string;
    input_freshness?: { market_data_age_seconds?: number | null; macro_calendar_age_minutes?: number | null };
    macro_event_window?: { active?: boolean; next_event?: string | null; minutes_until?: number | null };
    reason_codes?: string[];
  };
  thesis_validator: {
    available: boolean;
    mode: string;
    flip_rate?: number | null;
    action_rate?: number | null;
    grounding_rate?: number | null;
    evaluated_count?: number;
    position_day_count?: number;
    trading_day_count?: number;
    executed_exit_count?: number;
    window_days?: number;
  };
  top_rankings: {
    top_sectors: Array<{
      sector_key: string;
      label: string;
      score: number | null;
      top_industries: Array<{
        industry_key: string;
        label: string;
        score: number | null;
      }>;
    }>;
  };
};

export function getDashboardKpis(): Promise<DashboardKpiPayload> {
  return fetchPolledJson<DashboardKpiPayload>('/api/dashboard-kpis', 'KPI cards');
}
