import { getDashboardKpis } from './dashboardKpisApi';
import type { DashboardKpiPayload } from './dashboardKpisApi';
import { formatDecimal, formatNumber, formatRate, formatToken } from './format';
import { LlmSpendPanel } from './llmSpend';
import PageHeader from './pageHeader';
import { Panel } from './ui/Panel';
import { usePolledResource } from './useRemoteResource';

type Validator = DashboardKpiPayload['thesis_validator'];

function ThesisValidator({ validator }: { validator: Validator }) {
  const mode = formatToken(validator?.mode, { placeholder: 'shadow' });

  if (!validator?.available) {
    return <p className="text-ink-2">{mode} mode · no validation runs yet; verdicts start after the first 15:30 ET check.</p>;
  }

  return (
    <>
      <p className="text-[15px] font-semibold">{mode} · flip {formatRate(validator.flip_rate)}</p>
      <p className="mt-1 text-ink-2">
        Action {formatRate(validator.action_rate)} · grounded {formatRate(validator.grounding_rate ?? null)} ·{' '}
        {validator.evaluated_count ?? 0} verdicts over {validator.trading_day_count ?? 0} days
      </p>
      <p className="mt-1 text-[11px] text-dim">Target: flip under 10%, action 3–25%, grounded over 95%.</p>
    </>
  );
}

/** Operational detail that used to crowd the Live page. */
export function SystemHealthView({ payload }: { payload: DashboardKpiPayload }) {
  const usage = payload.usage_summary;
  const sectors = payload.top_rankings.top_sectors;

  return (
    <div className="flex flex-wrap gap-3">
      <Panel title="People using it" className="flex-[1_1_300px]">
        <p className="text-[15px] font-semibold">{formatNumber(usage.active_install_count, { digits: 0 })} active {usage.active_install_count === 1 ? 'install' : 'installs'}</p>
        <p className="mt-1 text-ink-2">seen on two days in the last 30 · {formatNumber(usage.unique_install_count, { digits: 0 })} install IDs seen, {formatNumber(usage.startup_ping_count, { digits: 0 })} startup pings</p>
      </Panel>
      <Panel title="Thesis validator" className="flex-[1_1_300px]">
        <ThesisValidator validator={payload.thesis_validator} />
      </Panel>
      <Panel title="Research rankings" meta="top sectors and their industries" className="flex-[2_1_640px]">
        {sectors.length === 0 ? <p className="text-dim">Rankings are unavailable right now.</p> : (
          <ol className="grid gap-3 sm:grid-cols-[repeat(auto-fit,minmax(200px,1fr))]">
            {sectors.map((sector, index) => (
              <li key={sector.sector_key}>
                <div className="flex justify-between gap-2 font-semibold">
                  <span>{index + 1}. {sector.label}</span>
                  <span className="text-dim">{formatDecimal(sector.score)}</span>
                </div>
                <ol className="mt-1 text-ink-2">
                  {sector.top_industries.map((industry) => (
                    <li key={industry.industry_key} className="flex justify-between gap-2">
                      <span>{industry.label}</span>
                      <span className="text-dim">{formatDecimal(industry.score)}</span>
                    </li>
                  ))}
                </ol>
              </li>
            ))}
          </ol>
        )}
      </Panel>
    </div>
  );
}

function SystemHealthPage() {
  const { payload, error } = usePolledResource(getDashboardKpis, { errorMessage: 'Failed to load system health.' });

  return (
    <main>
      <PageHeader title="Health" description="Who is running the bot, how the thesis validator is doing, what the research ranking currently favours, and what the models cost." />
      {error ? <p className="text-error">{error}</p> : null}
      {payload ? <SystemHealthView payload={payload} /> : null}
      {!payload && !error ? <p className="text-dim">Loading…</p> : null}
      <div className="mt-3"><LlmSpendPanel /></div>
    </main>
  );
}

export default SystemHealthPage;
