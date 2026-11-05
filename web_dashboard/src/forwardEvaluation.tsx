import { formatDate, formatDecimal, formatNumber, formatRate, formatSignedCurrency, formatToken } from './format';
import { getDemoForwardEvaluation, getForwardEvaluation } from './forwardEvaluationApi';
import type { Cohort, Comparison, DiscoveryLedger, ForwardEvaluationPayload, ResearchRoute, StrategySummary } from './forwardEvaluationApi';
import type { Column } from './ui/DataTable';
import { DataTable } from './ui/DataTable';
import { Meter } from './ui/Meter';
import { Panel } from './ui/Panel';
import { Tag } from './ui/Tag';
import type { TagTone } from './ui/Tag';
import { usePolledResource } from './useRemoteResource';

/**
 * Whether the evidence a promotion needs is actually accumulating.
 *
 * Every cohort freezes its acceptance criteria when it opens, so the verdict
 * shown here is judged by rules written before any of its outcomes existed. A
 * comparison stays interim until its window has closed and every exit is known.
 */

const VERDICTS: Record<Comparison['verdict'], { label: string; tone: TagTone }> = {
  interim: { label: 'Interim, window still open', tone: 'neutral' },
  accepted: { label: 'Met every pre-registered criterion', tone: 'gain' },
  not_accepted: { label: 'Did not meet its criteria', tone: 'loss' },
};

/** Admitted events lead; everything not evaluated is the quiet remainder. */
const ROUTE_STATUS_COLORS: Record<string, string> = { admitted: 'bg-info', not_admitted: 'bg-[#6e6e76]' };

function formatDays(seconds: number | null | undefined): string {
  return seconds === null || seconds === undefined ? 'N/A' : `${formatDecimal(seconds / 86400, { digits: 1 })} d`;
}

function count(value: number): string {
  return formatNumber(value, { digits: 0 });
}

const COHORT_COLUMNS: Column<Cohort>[] = [
  { key: 'cohort', header: 'Cohort', render: (cohort) => <>
    <span className="font-medium">{cohort.challenger_id ? 'Incumbent vs challenger' : 'Production'}</span>
    <span className="block font-mono text-[10px] text-dim">{cohort.base_pipeline_version.slice(0, 18)}…</span>
  </> },
  { key: 'window', header: 'Window', render: (cohort) => <span className="whitespace-nowrap">{formatDate(cohort.window_start)} – {formatDate(cohort.window_end)}</span> },
  { key: 'enrolled', header: 'Enrolled · resolved', render: (cohort) => <div className="flex min-w-48 flex-col gap-1.5">
    {Object.entries(cohort.runs).map(([role, run]) => <div key={role}>
      <Meter label={`${role} enrolled`} value={run.enrolled} max={run.target_candidates || 1} />
      <span className="text-[11px] text-dim">{run.resolved} resolved · {run.status}</span>
    </div>)}
  </div> },
];

const STRATEGY_COLUMNS: Column<[string, StrategySummary]>[] = [
  { key: 'strategy', header: 'Strategy (paired decisions only)', render: ([label]) => label },
  { key: 'pnl', header: 'Net P/L', align: 'right', render: ([, summary]) => <span className={summary.net_pnl > 0 ? 'text-gain' : 'text-loss'}>{formatSignedCurrency(summary.net_pnl)}</span> },
  { key: 'return', header: 'Net return', align: 'right', render: ([, summary]) => formatRate(summary.net_return, { digits: 1 }) },
  { key: 'decisions', header: 'Decisions', align: 'right', render: ([, summary]) => `${summary.trades} trades · ${summary.cash} cash` },
  { key: 'drawdown', header: 'Max drawdown', align: 'right', render: ([, summary]) => formatSignedCurrency(summary.max_drawdown ? -summary.max_drawdown : 0) },
];

function ComparisonPanel({ comparison }: { comparison: Comparison }) {
  const definition = comparison.challenger_definition;
  const verdict = VERDICTS[comparison.verdict];
  return <Panel
    title={definition ? `${definition.strategist_model || 'same strategist'} / ${definition.manager_model || 'same manager'}` : 'Challenger'}
    meta={`report ${comparison.report_id.slice(11, 23)} · as of ${formatDate(comparison.information_cutoff_time)}`}
    actions={<Tag tone={verdict.tone}>{verdict.label}</Tag>}
    flush
  >
    <div className="flex flex-col gap-1 px-3.5 py-2.5 text-xs text-ink-2">
      <p>{comparison.n_paired} paired of {comparison.observations.recorded} observations · {comparison.n_clusters} independent clusters · coverage {formatRate(comparison.coverage)} · regimes {comparison.regimes.join(', ') || 'none known'}</p>
      <p>Challenger minus incumbent: <b>{formatSignedCurrency(comparison.paired_difference)}</b> · per decision {formatSignedCurrency(comparison.mean_paired_difference)} · cluster-bootstrap lower bound {formatSignedCurrency(comparison.bootstrap.lower_bound)}</p>
    </div>
    <DataTable label="Paired strategies" columns={STRATEGY_COLUMNS} rows={[['Incumbent', comparison.incumbent], ['Challenger', comparison.challenger]]} rowKey={([label]) => label} />
    <ul aria-label="Acceptance criteria" className="flex flex-wrap gap-x-4 gap-y-1 px-3.5 py-2.5 text-xs">
      {comparison.criteria.map((criterion) => <li key={criterion.criterion} className={criterion.passed ? 'text-gain' : 'text-loss'}>
        {criterion.passed ? '✓' : '✗'} <span className="text-ink-2">{formatToken(criterion.criterion)}</span>
      </li>)}
    </ul>
  </Panel>
}

function RouteBar({ route, counts }: { route: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  const total = entries.reduce((sum, [, n]) => sum + n, 0) || 1;
  return <div role="img" aria-label={`${route} route: ${entries.map(([status, n]) => `${count(n)} ${formatToken(status)}`).join(', ')}`} className="flex h-3 overflow-hidden rounded-sm bg-base-300">
    {entries.map(([status, n]) => <span key={status} className={ROUTE_STATUS_COLORS[status] ?? 'bg-base-300'} style={{ width: `${(n / total) * 100}%`, minWidth: n ? 2 : 0 }} />)}
  </div>
}

function DiscoverySummary({ discovery }: { discovery: DiscoveryLedger }) {
  return <div className="flex flex-col gap-3">
    <p className="text-xs text-ink-2">{count(discovery.admitted_union)} of {count(discovery.events_seen)} events admitted by at least one route.</p>
    {Object.entries(discovery.route_status_counts).map(([route, counts]) => <div key={route} className="flex flex-col gap-1">
      <div className="flex justify-between text-[11px]"><span className="text-ink-2">{formatToken(route)} route</span><span className="text-dim">median admission {formatDays(discovery.admission_latency_seconds_median[route])}</span></div>
      <RouteBar route={route} counts={counts} />
      <p className="text-[11px] text-dim">{Object.entries(counts).map(([status, n]) => `${count(n)} ${formatToken(status)}`).join(' · ')}</p>
    </div>)}
    {Object.entries(discovery.outcomes_by_admission).map(([group, horizons]) => <p key={group} className="text-[11px] text-dim">
      {formatToken(group)}: 5-day signed residual {formatRate(horizons['5d']?.mean_signed_residual_return, { digits: 2 })} over {horizons['5d']?.resolved ?? 0} resolved
    </p>)}
  </div>
}

function ResearchSummary({ research }: { research: ResearchRoute }) {
  return <div className="flex flex-col gap-1 text-xs text-ink-2">
    <p>{research.theses} directional shadow theses · {count(research.research_cost.model_and_retrieval_calls)} calls · median session-to-thesis {formatDays(research.latency_seconds.session_to_thesis_median)}</p>
    {Object.entries(research.outcomes_by_check_status).map(([status, outcome]) => <p key={status} className="text-[11px] text-dim">
      {formatToken(status)}: signed residual {formatRate(outcome.mean_signed_residual_return, { digits: 2 })}, hit rate {formatRate(outcome.hit_rate)} over {outcome.resolved} resolved
    </p>)}
  </div>
}

function CollectionHealth({ payload }: { payload: ForwardEvaluationPayload }) {
  const observations = payload.decision_observations ?? {};
  const latestMarks = payload.mark_sessions?.[0];
  return <div className="flex flex-col gap-1 text-xs text-ink-2">
    <p>{observations.recorded ?? 0} decision observations frozen · {observations.unavailable ?? 0} unavailable</p>
    {(payload.paired_evaluations ?? []).map((row) => <p key={`${row.role}:${row.status}`} className="text-[11px] text-dim">
      {row.role} {formatToken(row.status)}: {row.n}{row.model_calls ? ` · ${row.model_calls} model calls` : ''}
    </p>)}
    <p>{latestMarks ? `Closing marks ${latestMarks.session_date}: ${latestMarks.status}, ${latestMarks.summary?.stored ?? 0} stored` : 'No closing-mark session yet'}</p>
  </div>
}

function ForwardEvaluationBody({ payload }: { payload: ForwardEvaluationPayload }) {
  const challenger = payload.settings.challenger;
  const challengerLine = challenger.mode === 'shadow'
    ? `challenger shadow · ${challenger.strategist_model || 'same strategist'} / ${challenger.manager_model || 'same manager'}`
    : `challenger ${challenger.configuration_error ?? challenger.mode ?? 'unknown'}`;
  return <>
    <Panel title="Forward-evaluation cohorts" meta={challengerLine} flush>
      <DataTable label="Forward cohorts" columns={COHORT_COLUMNS} rows={payload.cohorts ?? []} rowKey={(cohort) => cohort.cohort_id} emptyMessage="No forward cohort has opened yet; the next production candidate opens one." />
    </Panel>
    {payload.comparisons?.length
      ? payload.comparisons.map((comparison) => <ComparisonPanel key={comparison.report_id} comparison={comparison} />)
      : <p className="text-[11px] text-dim">No challenger comparison yet. Set <code className="font-mono">RESEARCH_CHALLENGER_MODE=shadow</code> to start one.</p>}
    <div className="flex flex-wrap gap-3">
      <Panel className="min-w-64 flex-[1_1_16rem]" title="Collection"><CollectionHealth payload={payload} /></Panel>
      {payload.discovery ? <Panel className="min-w-72 flex-[2_1_24rem]" title="Discovery ledger" meta={`last ${payload.window_days ?? 30} days`}><DiscoverySummary discovery={payload.discovery} /></Panel> : null}
      {payload.research_route ? <Panel className="min-w-64 flex-[1_1_16rem]" title="Research route"><ResearchSummary research={payload.research_route} /></Panel> : null}
    </div>
  </>
}

function ForwardEvaluationPanel({ demoMode = false }: { demoMode?: boolean }) {
  const { payload, error } = usePolledResource<ForwardEvaluationPayload>(
    demoMode ? getDemoForwardEvaluation : getForwardEvaluation,
    { errorMessage: 'Failed to load forward evaluation.' },
  );
  if (error) return <p className="text-xs text-error">{error}</p>;
  if (!payload) return <p className="text-dim">Reading forward evaluation…</p>;
  if (!payload.available) return <p className="text-dim">{payload.reason}</p>;
  return <ForwardEvaluationBody payload={payload} />;
}

export default ForwardEvaluationPanel;
