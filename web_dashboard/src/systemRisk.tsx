import { getDashboardKpis } from './dashboardKpisApi';
import type { DashboardKpiPayload } from './dashboardKpisApi';
import { formatSignedPercent } from './format';
import PageHeader from './pageHeader';
import RiskControlsPanel from './riskControls';
import { Panel } from './ui/Panel';
import { usePolledResource } from './useRemoteResource';

type ExitRules = DashboardKpiPayload['option_exit_rules'];

/** Take-profit and stop-loss targets per days-to-expiry bucket. */
export function ExitLadders({ rules }: { rules: ExitRules }) {
  return (
    <Panel title="Option exit ladders" meta="by days to expiry">
      <dl className="grid gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-[11px] text-dim">Take profit</dt>
          <dd className="text-gain-text">{rules.take_profit_summary || 'Not configured'}</dd>
          <dd className="text-[11px] text-dim">Outside the buckets: {formatSignedPercent(rules.fallback_take_profit_pct, { digits: 0 })}</dd>
        </div>
        <div>
          <dt className="text-[11px] text-dim">Stop loss</dt>
          <dd className="text-loss-text">{rules.stop_loss_summary || 'Not configured'}</dd>
          <dd className="text-[11px] text-dim">Outside the buckets: {formatSignedPercent(rules.fallback_stop_loss_pct, { digits: 0 })}</dd>
        </div>
      </dl>
    </Panel>
  );
}

function SystemRiskPage() {
  const { payload, error } = usePolledResource(getDashboardKpis, { errorMessage: 'Failed to load exit rules.' });

  return (
    <main className="flex flex-col gap-3">
      <PageHeader title="Risk" description="The exit rules every open option follows, and the risk settings the bot reads. Read-only." />
      {error ? <p className="text-error">{error}</p> : null}
      {payload ? <ExitLadders rules={payload.option_exit_rules} /> : null}
      <RiskControlsPanel />
    </main>
  );
}

export default SystemRiskPage;
