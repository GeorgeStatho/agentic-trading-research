import { useEffect, useState } from 'react';
import './kpi.css';

type DashboardKpiPayload = {
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
  bot_status: {
    state: string;
    label: string;
    detail: string;
  };
  market_status: {
    state: string;
    label: string;
    detail: string;
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

type KpiCardConfig = {
  title: string;
  value: string;
  detail: string;
  tone?: 'neutral' | 'positive' | 'negative' | 'status';
  state?: string;
  compactValue?: boolean;
};

const KPI_POLL_INTERVAL_MS = 60_000;

async function getDashboardKpis(): Promise<DashboardKpiPayload> {
  const response = await fetch(`/api/dashboard-kpis?ts=${Date.now()}`);

  if (!response.ok) {
    throw new Error(`Failed to load KPI cards: ${response.status}`);
  }

  return response.json() as Promise<DashboardKpiPayload>;
}

function formatCurrency(value: number | null, options?: Intl.NumberFormatOptions): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }

  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
    ...options,
  }).format(value);
}

function formatSignedCurrency(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }

  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
    signDisplay: 'always',
  }).format(value);
}

function formatPercent(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }

  return `${value.toFixed(2)}%`;
}

function formatInteger(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }

  return new Intl.NumberFormat().format(value);
}

function formatRankingScore(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }
  return value.toFixed(2);
}

function formatRatio(value: number | null, digits = 2): string {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }
  return value.toFixed(digits);
}

function getDayPlTone(dayPl: number | null): KpiCardConfig['tone'] {
  if (dayPl === null || Number.isNaN(dayPl)) {
    return 'neutral';
  }

  if (dayPl > 0) {
    return 'positive';
  }
  if (dayPl < 0) {
    return 'negative';
  }
  return 'neutral';
}

function getStatusState(state: string): string {
  return state.trim().toLowerCase().replace(/\s+/g, '-');
}

function buildCards(payload: DashboardKpiPayload): KpiCardConfig[] {
  return [
    {
      title: 'Account Equity',
      value: formatCurrency(payload.account_equity),
      detail: 'Live account equity',
    },
    {
      title: 'Buying Power',
      value: formatCurrency(payload.buying_power),
      detail: 'Available to deploy',
    },
    {
      title: 'Day P/L',
      value: formatSignedCurrency(payload.day_pl),
      detail: payload.day_pl_pct === null ? 'No day-over-day baseline' : `${formatPercent(payload.day_pl_pct)} vs last equity`,
      tone: getDayPlTone(payload.day_pl),
    },
    {
      title: 'Open Positions',
      value: formatInteger(payload.open_positions),
      detail: 'All active Alpaca positions',
    },
    {
      title: 'Options Exposure',
      value: formatCurrency(payload.options_exposure.market_value),
      detail:
        payload.options_exposure.equity_pct === null
          ? `${formatInteger(payload.options_exposure.position_count)} option positions`
          : `${formatPercent(payload.options_exposure.equity_pct)} of equity across ${formatInteger(payload.options_exposure.position_count)} positions`,
    },
    {
      title: 'Option Take-Profit',
      value: payload.option_exit_rules.take_profit_summary || 'N/A',
      detail:
        payload.option_exit_rules.fallback_take_profit_pct === null
          ? 'Legacy fallback target unavailable'
          : `Fallback target outside DTE buckets: +${payload.option_exit_rules.fallback_take_profit_pct.toFixed(0)}%`,
      tone: 'positive',
      compactValue: true,
    },
    {
      title: 'Option Stop-Loss',
      value: payload.option_exit_rules.stop_loss_summary || 'N/A',
      detail:
        payload.option_exit_rules.fallback_stop_loss_pct === null
          ? 'Legacy fallback floor unavailable'
          : `Fallback floor outside DTE buckets: ${payload.option_exit_rules.fallback_stop_loss_pct.toFixed(0)}%`,
      tone: 'negative',
      compactValue: true,
    },
    {
      title: 'Win Rate',
      value:
        payload.win_rate.win_rate_pct === null
          ? 'N/A'
          : `${payload.win_rate.win_rate_pct.toFixed(1)}%`,
      detail:
        payload.win_rate.closed_trades === 0
          ? 'No closed fills yet'
          : `${formatInteger(payload.win_rate.wins)} wins from ${formatInteger(payload.win_rate.closed_trades)} closed trades`,
      tone:
        payload.win_rate.win_rate_pct !== null && payload.win_rate.win_rate_pct >= 50
          ? 'positive'
          : 'neutral',
    },
    {
      title: 'Max Drawdown',
      value: formatPercent(payload.max_drawdown_pct),
      detail: 'Peak-to-trough across current chart window',
      tone:
        payload.max_drawdown_pct !== null && payload.max_drawdown_pct > 10
          ? 'negative'
          : 'neutral',
    },
    {
      title: 'Performance Metrics',
      value: `Sharpe ${formatRatio(payload.performance_summary.annualized_sharpe)} / Sortino ${formatRatio(payload.performance_summary.annualized_sortino)}`,
      detail: `Total return ${formatPercent(payload.performance_summary.total_return_pct)} / Avg trade ${formatPercent(payload.performance_summary.average_trade_return_pct)} / Profit factor ${formatRatio(payload.performance_summary.profit_factor)}`,
      compactValue: true,
    },
    {
      title: 'Bot Status',
      value: payload.bot_status.label,
      detail: payload.bot_status.detail || 'No bot heartbeat yet',
      tone: 'status',
      state: getStatusState(payload.bot_status.state),
    },
    {
      title: 'Market Status',
      value: payload.market_status.label,
      detail: payload.market_status.detail || 'Clock unavailable',
      tone: 'status',
      state: getStatusState(payload.market_status.state),
    },
  ];
}

function DashboardKpis() {
  const [payload, setPayload] = useState<DashboardKpiPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const loadKpis = () => {
      getDashboardKpis()
        .then((nextPayload) => {
          if (!isMounted) {
            return;
          }

          setPayload(nextPayload);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!isMounted) {
            return;
          }

          setError(err instanceof Error ? err.message : 'Failed to load dashboard KPIs.');
        });
    };

    loadKpis();
    const intervalId = window.setInterval(loadKpis, KPI_POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

  if (error) {
    return (
      <section className="kpi-grid kpi-grid--error">
        <article className="kpi-card kpi-card--negative">
          <p className="kpi-card__label">Dashboard KPIs</p>
          <p className="kpi-card__value">Unavailable</p>
          <p className="kpi-card__detail">{error}</p>
        </article>
      </section>
    );
  }

  if (!payload) {
    return (
      <section className="kpi-grid">
        <article className="kpi-card">
          <p className="kpi-card__label">Dashboard KPIs</p>
          <p className="kpi-card__value">Loading...</p>
          <p className="kpi-card__detail">Fetching live account and bot metrics.</p>
        </article>
      </section>
    );
  }

  const cards = buildCards(payload);

  return (
    <>
      <section className="kpi-grid" aria-label="Top KPI cards">
        {cards.map((card) => (
          <article
            key={card.title}
            className={`kpi-card kpi-card--${card.tone ?? 'neutral'}${card.state ? ` kpi-card--state-${card.state}` : ''}${card.compactValue ? ' kpi-card--compact' : ''}`}
          >
            <p className="kpi-card__label">{card.title}</p>
            <p className="kpi-card__value">{card.value}</p>
            <p className="kpi-card__detail">{card.detail}</p>
          </article>
        ))}
      </section>

      <section className="kpi-grid kpi-grid--rankings" aria-label="Current top ranked sectors and industries">
        <article className="kpi-card kpi-card--compact">
          <p className="kpi-card__label">Top 3 Sectors</p>
          <div className="kpi-ranking-list">
            {payload.top_rankings.top_sectors.length > 0 ? (
              payload.top_rankings.top_sectors.map((sector, index) => (
                <div key={sector.sector_key} className="kpi-ranking-item">
                  <div>
                    <p className="kpi-ranking-item__title">
                      {index + 1}. {sector.label}
                    </p>
                    <p className="kpi-ranking-item__meta">{sector.sector_key}</p>
                  </div>
                  <span className="kpi-ranking-item__score">{formatRankingScore(sector.score)}</span>
                </div>
              ))
            ) : (
              <p className="kpi-card__detail">Sector rankings are unavailable right now.</p>
            )}
          </div>
        </article>

        <article className="kpi-card kpi-card--compact">
          <p className="kpi-card__label">Top 3 Industries Per Sector</p>
          <div className="kpi-ranking-list">
            {payload.top_rankings.top_sectors.length > 0 ? (
              payload.top_rankings.top_sectors.map((sector) => (
                <div key={`industries-${sector.sector_key}`} className="kpi-ranking-item kpi-ranking-item--stacked">
                  <div>
                    <p className="kpi-ranking-item__title">{sector.label}</p>
                    <div className="kpi-ranking-sublist">
                      {sector.top_industries.length > 0 ? (
                        sector.top_industries.map((industry, index) => (
                          <div key={`${sector.sector_key}-${industry.industry_key}`} className="kpi-ranking-subitem">
                            <span className="kpi-ranking-subitem__label">
                              {index + 1}. {industry.label}
                            </span>
                            <span className="kpi-ranking-subitem__score">{formatRankingScore(industry.score)}</span>
                          </div>
                        ))
                      ) : (
                        <p className="kpi-ranking-item__meta">No ranked industries available.</p>
                      )}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <p className="kpi-card__detail">Industry rankings are unavailable right now.</p>
            )}
          </div>
        </article>
      </section>
    </>
  );
}

export default DashboardKpis;
