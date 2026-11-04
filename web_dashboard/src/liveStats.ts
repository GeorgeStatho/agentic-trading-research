import type { DashboardKpiPayload } from './dashboardKpisApi';
import {
  formatCurrency,
  formatPercent,
  formatSignedCurrency,
  formatSignedPercent,
  formatToken,
  hasNumericValue,
} from './format';
import type { Stat } from './ui/StatStrip';

/** Below this many closed trades a rate is shown muted with its n= (REDESIGN.md rule 7). */
const MIN_CLOSED_TRADES = 5;

function signTone(value: number | null | undefined): Stat['tone'] {
  if (!hasNumericValue(value) || value === 0) {
    return undefined;
  }
  return value > 0 ? 'gain' : 'loss';
}

/** The Live page's runtime-stats strip, built from `/api/dashboard-kpis`. */
export function buildLiveStats(payload: DashboardKpiPayload): Stat[] {
  const { closed_trades: closed, wins, win_rate_pct: winRatePct } = payload.win_rate;
  const smallSample = closed < MIN_CLOSED_TRADES;
  const expectancy = payload.performance_summary.average_trade_return_pct;
  const exposure = payload.options_exposure;

  const stats: Stat[] = [
    { label: 'Equity', value: formatCurrency(payload.account_equity) },
    {
      label: 'Day P/L',
      value: formatSignedCurrency(payload.day_pl),
      sub: formatSignedPercent(payload.day_pl_pct),
      tone: signTone(payload.day_pl),
    },
    {
      label: 'Total return',
      value: formatSignedPercent(payload.performance_summary.total_return_pct),
      tone: signTone(payload.performance_summary.total_return_pct),
    },
  ];

  if (payload.unrealized_pl !== undefined) {
    stats.push({ label: 'Unrealized', value: formatSignedCurrency(payload.unrealized_pl), tone: signTone(payload.unrealized_pl) });
  }

  stats.push(
    {
      label: 'Holdings',
      value: formatCurrency(exposure.market_value),
      sub: hasNumericValue(exposure.equity_pct) ? `${formatPercent(exposure.equity_pct)} of equity` : undefined,
    },
    { label: 'Buying power', value: formatCurrency(payload.buying_power) },
    {
      label: 'Max drawdown',
      value: formatPercent(payload.max_drawdown_pct),
      tone: hasNumericValue(payload.max_drawdown_pct) && payload.max_drawdown_pct > 0 ? 'loss' : undefined,
    },
    {
      label: 'Win rate',
      value: `${wins} / ${closed}`,
      sub: smallSample ? `n=${closed}` : formatPercent(winRatePct, { digits: 0 }),
      tone: smallSample ? 'muted' : undefined,
    },
    {
      label: 'Expectancy',
      value: formatSignedPercent(expectancy),
      sub: smallSample ? `n=${closed}` : 'avg per trade',
      tone: smallSample ? 'muted' : signTone(expectancy),
    },
    { label: 'Regime', value: formatToken(payload.market_regime.state, { placeholder: 'Unknown' }) },
  );

  return stats;
}
