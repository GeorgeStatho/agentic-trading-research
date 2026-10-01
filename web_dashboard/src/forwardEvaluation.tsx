import { formatDate, formatDecimal, formatNumber, formatRate, formatSignedCurrency, formatToken } from './format';
import { usePolledResource } from './useRemoteResource';
import { getDemoForwardEvaluation, getForwardEvaluation } from './forwardEvaluationApi';
import type {
  Cohort,
  Comparison,
  Criterion,
  DiscoveryLedger,
  ForwardEvaluationPayload,
  ResearchRoute,
  StrategySummary,
} from './forwardEvaluationApi';
import './forwardEvaluation.css';

/**
 * Whether the evidence a promotion needs is actually accumulating.
 *
 * Every cohort freezes its acceptance criteria when it opens, so the verdict
 * shown here is judged by rules written before any of its outcomes existed. A
 * comparison stays interim until its window has closed and every exit is known.
 */

const VERDICT_LABELS: Record<Comparison['verdict'], string> = {
  interim: 'interim — window still open',
  accepted: 'met every pre-registered criterion',
  not_accepted: 'did not meet its criteria',
};

function formatDays(seconds: number | null | undefined): string {
  return seconds === null || seconds === undefined ? 'N/A' : `${formatDecimal(seconds / 86400, { digits: 1 })} d`;
}

function CohortRow({ cohort }: { cohort: Cohort }) {
  return (
    <tr>
      <td>
        <strong>{cohort.challenger_id ? 'Incumbent vs challenger' : 'Production'}</strong>
        <small>{cohort.base_pipeline_version.slice(0, 18)}…</small>
      </td>
      <td>
        {formatDate(cohort.window_start)} – {formatDate(cohort.window_end)}
      </td>
      <td>
        {Object.entries(cohort.runs).map(([role, run]) => (
          <small key={role}>
            {role}: {run.enrolled} enrolled · {run.resolved} resolved · {run.status}
          </small>
        ))}
      </td>
    </tr>
  );
}

function CriterionRow({ criterion }: { criterion: Criterion }) {
  return (
    <li className={criterion.passed ? 'forward-eval__met' : 'forward-eval__unmet'}>
      <span>{criterion.passed ? '✓' : '✗'}</span> {formatToken(criterion.criterion)}
    </li>
  );
}

function StrategyLine({ label, summary }: { label: string; summary: StrategySummary }) {
  return (
    <tr>
      <td>{label}</td>
      <td className={summary.net_pnl > 0 ? 'v2-status__positive' : 'v2-status__negative'}>
        {formatSignedCurrency(summary.net_pnl)}
      </td>
      <td>{formatRate(summary.net_return, { digits: 1 })}</td>
      <td>
        {summary.trades} trades · {summary.cash} cash
      </td>
      <td>{formatSignedCurrency(summary.max_drawdown ? -summary.max_drawdown : 0)}</td>
    </tr>
  );
}

function ComparisonCard({ comparison }: { comparison: Comparison }) {
  const definition = comparison.challenger_definition;
  return (
    <article className="forward-eval__comparison">
      <header>
        <div>
          <strong>
            {definition ? `${definition.strategist_model || 'same strategist'} / ${definition.manager_model || 'same manager'}` : 'Challenger'}
          </strong>
          <small>
            Report {comparison.report_id.slice(11, 23)} · as of {formatDate(comparison.information_cutoff_time)}
          </small>
        </div>
        <span className={`v2-status__pill forward-eval__verdict--${comparison.verdict}`}>{VERDICT_LABELS[comparison.verdict]}</span>
      </header>
      <p className="v2-status__report-line">
        {comparison.n_paired} paired of {comparison.observations.recorded} observations · {comparison.n_clusters} independent
        clusters · coverage {formatRate(comparison.coverage)} · regimes {comparison.regimes.join(', ') || 'none known'}
      </p>
      <p className="v2-status__report-line">
        Challenger minus incumbent: <b>{formatSignedCurrency(comparison.paired_difference)}</b> · per decision{' '}
        {formatSignedCurrency(comparison.mean_paired_difference)} · cluster-bootstrap lower bound{' '}
        {formatSignedCurrency(comparison.bootstrap.lower_bound)}
      </p>
      <div className="v2-status__table-wrap">
        <table className="v2-status__gates">
          <thead>
            <tr>
              <th>Strategy (paired decisions only)</th>
              <th>Net P&amp;L</th>
              <th>Net return</th>
              <th>Decisions</th>
              <th>Max drawdown</th>
            </tr>
          </thead>
          <tbody>
            <StrategyLine label="Incumbent" summary={comparison.incumbent} />
            <StrategyLine label="Challenger" summary={comparison.challenger} />
          </tbody>
        </table>
      </div>
      <ul className="forward-eval__criteria">
        {comparison.criteria.map((criterion) => (
          <CriterionRow key={criterion.criterion} criterion={criterion} />
        ))}
      </ul>
    </article>
  );
}

function DiscoverySummary({ discovery }: { discovery: DiscoveryLedger }) {
  return (
    <article>
      <strong>Discovery ledger</strong>
      <p>
        {discovery.admitted_union} of {discovery.events_seen} events admitted by at least one route.
      </p>
      {Object.entries(discovery.route_status_counts).map(([route, counts]) => (
        <p key={route} className="v2-status__muted">
          {route}: {Object.entries(counts).map(([status, n]) => `${n} ${formatToken(status)}`).join(' · ')} · median
          admission {formatDays(discovery.admission_latency_seconds_median[route])}
        </p>
      ))}
      {Object.entries(discovery.outcomes_by_admission).map(([group, horizons]) => (
        <p key={group}>
          {formatToken(group)}: 5-day signed residual {formatRate(horizons['5d']?.mean_signed_residual_return, { digits: 2 })} over{' '}
          {horizons['5d']?.resolved ?? 0} resolved
        </p>
      ))}
    </article>
  );
}

function ResearchSummary({ research }: { research: ResearchRoute }) {
  return (
    <article>
      <strong>Research route</strong>
      <p>
        {research.theses} directional shadow theses · {formatNumber(research.research_cost.model_and_retrieval_calls, { digits: 0 })}{' '}
        calls · median session-to-thesis {formatDays(research.latency_seconds.session_to_thesis_median)}
      </p>
      {Object.entries(research.outcomes_by_check_status).map(([status, outcome]) => (
        <p key={status}>
          {formatToken(status)}: signed residual {formatRate(outcome.mean_signed_residual_return, { digits: 2 })}, hit rate{' '}
          {formatRate(outcome.hit_rate)} over {outcome.resolved} resolved
        </p>
      ))}
    </article>
  );
}

function CollectionHealth({ payload }: { payload: ForwardEvaluationPayload }) {
  const observations = payload.decision_observations ?? {};
  const latestMarks = payload.mark_sessions?.[0];
  return (
    <article>
      <strong>Collection</strong>
      <p>
        {observations.recorded ?? 0} decision observations frozen · {observations.unavailable ?? 0} unavailable
      </p>
      {(payload.paired_evaluations ?? []).map((row) => (
        <p key={`${row.role}:${row.status}`} className="v2-status__muted">
          {row.role} {formatToken(row.status)}: {row.n}
          {row.model_calls ? ` · ${row.model_calls} model calls` : ''}
        </p>
      ))}
      <p>
        {latestMarks
          ? `Closing marks ${latestMarks.session_date}: ${latestMarks.status}, ${latestMarks.summary?.stored ?? 0} stored`
          : 'No closing-mark session yet'}
      </p>
    </article>
  );
}

function ForwardEvaluationBody({ payload }: { payload: ForwardEvaluationPayload }) {
  const challenger = payload.settings.challenger;
  return (
    <>
      <section className="v2-status__card">
        <header>
          <div>
            <p>Forward evaluation</p>
            <h2>Cohorts and pre-registered comparisons</h2>
          </div>
          <span>
            Challenger {challenger.configuration_error ?? challenger.mode ?? 'unknown'}
            {challenger.mode === 'shadow' ? ` · ${challenger.strategist_model || 'same strategist'} / ${challenger.manager_model || 'same manager'}` : ''}
          </span>
        </header>
        {payload.cohorts?.length ? (
          <div className="v2-status__table-wrap">
            <table className="v2-status__gates">
              <thead>
                <tr>
                  <th>Cohort</th>
                  <th>Evaluation window</th>
                  <th>Runs</th>
                </tr>
              </thead>
              <tbody>
                {payload.cohorts.map((cohort) => (
                  <CohortRow key={cohort.cohort_id} cohort={cohort} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="forward-eval__empty">No forward cohort has opened yet; the next production candidate opens one.</p>
        )}
        <div className="v2-status__runs">
          {payload.comparisons?.length ? (
            payload.comparisons.map((comparison) => <ComparisonCard key={comparison.report_id} comparison={comparison} />)
          ) : (
            <p className="v2-status__muted">No challenger comparison yet. Set RESEARCH_CHALLENGER_MODE=shadow to start one.</p>
          )}
        </div>
      </section>

      <section className="v2-status__card">
        <header>
          <div>
            <p>Routes</p>
            <h2>Collection, discovery and research</h2>
          </div>
          <span>Last {payload.window_days ?? 30} days</span>
        </header>
        <div className="v2-status__shadow-grid">
          <CollectionHealth payload={payload} />
          {payload.discovery ? <DiscoverySummary discovery={payload.discovery} /> : null}
          {payload.research_route ? <ResearchSummary research={payload.research_route} /> : null}
        </div>
      </section>
    </>
  );
}

function ForwardEvaluationPanel({ demoMode = false }: { demoMode?: boolean }) {
  const { payload, error } = usePolledResource<ForwardEvaluationPayload>(
    demoMode ? getDemoForwardEvaluation : getForwardEvaluation,
    { errorMessage: 'Failed to load forward evaluation.' },
  );
  if (error) {
    return <section className="v2-status__empty">{error}</section>;
  }
  if (!payload) {
    return <section className="v2-status__empty">Reading forward evaluation…</section>;
  }
  if (!payload.available) {
    return <section className="v2-status__empty">{payload.reason}</section>;
  }
  return <ForwardEvaluationBody payload={payload} />;
}

export default ForwardEvaluationPanel;
