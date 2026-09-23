import { fetchPolledJson } from './api';
import { formatCurrency, formatDate } from './format';
import { usePolledResource } from './useRemoteResource';
import './v2Status.css';

/**
 * What the V2 architecture is actually doing right now.
 *
 * The distinction this page exists for: asking for `enforce` does not make a
 * gate enforce. A control only takes effect once its stages carry a promotion
 * proof bound to the current thresholds and source, so every row shows the
 * requested mode, the effective mode, and the evidence still outstanding.
 */

type Activity = {
  observed_candidates: number;
  shadow_blocks: number;
  enforced_blocks: number;
};

type Proof = {
  policy_hash: string;
  report_id: string;
  information_cutoff_time: string;
  sample_size: number;
  gate_value: number;
};

type Stage = {
  stage: string;
  enforcement_allowed: boolean;
  proof: Proof | null;
  superseded_proofs: number;
  activity: Activity | null;
};

type ScenarioDetail = {
  observations: number;
  scored: number;
  different_contract: number;
  basis: string;
};

type Feature = {
  key: string;
  label: string;
  description: string;
  env_var: string;
  requested_mode: string;
  effective_mode: string | null;
  downgraded: boolean;
  activity_basis: string;
  stages: Stage[];
  detail: ScenarioDetail | null;
  configuration_error?: string;
};

type ShadowOnlyFeature = {
  key: string;
  label: string;
  stage: string;
  description: string;
  reason: string;
  activity: Activity | null;
};

type GateReadiness = {
  stage: string;
  n_seen: number | null;
  n_evaluable: number | null;
  n_passed: number | null;
  n_rejected: number | null;
  n_missing_outcomes: number | null;
  regimes: string[];
  gate_value: number | null;
  losses_avoided: number | null;
  profits_sacrificed: number | null;
  blocking_reasons: string[];
};

type CohortReport = {
  report_id: string;
  information_cutoff_time: string;
  n_observed: number | null;
  covers_whole_cohort: boolean;
  paired_regimes: string[];
  gates: GateReadiness[];
};

type Run = {
  run_id: string;
  kind: string;
  status: string;
  started_at: string;
  target_candidates: number;
  enrolled_candidates: number;
  promotion_sample_floor: number | null;
  code_hash_matches: boolean;
  latest_full_cohort_report: CohortReport | null;
};

type Research = {
  available: boolean;
  reason?: string;
  runs: Run[];
  champion: { strategy: string; run_id: string | null; information_cutoff_time: string } | null;
};

type Prerequisite = {
  key: string;
  label: string;
  status: string;
  value: string;
  detail: string;
};

type Payload = {
  as_of: string;
  window_days: number;
  source_hash: string | null;
  mode_summary: { enforcing: number; shadow: number; disabled: number; downgraded: number; misconfigured: number };
  activity_available: boolean;
  features: Feature[];
  shadow_only_features: ShadowOnlyFeature[];
  research: Research;
  prerequisites: Prerequisite[];
};

function getV2Status(): Promise<Payload> {
  return fetchPolledJson<Payload>('/api/v2-status', 'V2 rollout status');
}

function ModeBadge({ mode, downgraded }: { mode: string | null; downgraded: boolean }) {
  const label = mode ?? 'unreadable';
  const tone = downgraded ? 'downgraded' : label;
  return <span className={`v2-mode v2-mode--${tone}`}>{label}</span>;
}

function ActivityCell({ activity, basis }: { activity: Activity | null; basis: string }) {
  if (!activity) {
    return (
      <span className="v2-status__muted">
        {basis === 'positions' ? 'Recorded per position, not per candidate' : 'No candidate stream recorded'}
      </span>
    );
  }
  return (
    <>
      <strong>{activity.shadow_blocks}</strong> would block
      <small>
        {activity.observed_candidates} candidates observed
        {activity.enforced_blocks > 0 ? ` · ${activity.enforced_blocks} actually blocked` : ''}
      </small>
    </>
  );
}

function StageCell({ stage }: { stage: Stage }) {
  return (
    <li>
      <span className="v2-status__stage-name">{stage.stage}</span>
      {stage.proof ? (
        <span className="v2-status__proof">
          proof: {stage.proof.sample_size} observations, {formatCurrency(stage.proof.gate_value)} gate value
        </span>
      ) : (
        <span className="v2-status__no-proof">no proof — enforcement stays shadow</span>
      )}
      {stage.superseded_proofs > 0 ? (
        <small>
          {stage.superseded_proofs} earlier proof{stage.superseded_proofs === 1 ? '' : 's'} no longer match the current
          thresholds or source
        </small>
      ) : null}
    </li>
  );
}

function FeatureRow({ feature }: { feature: Feature }) {
  return (
    <tr className={feature.downgraded ? 'v2-status__row--warn' : ''}>
      <td>
        <strong>{feature.label}</strong>
        <small>{feature.description}</small>
        <code>{feature.env_var}</code>
      </td>
      <td>
        <ModeBadge mode={feature.effective_mode} downgraded={feature.downgraded} />
        {feature.requested_mode !== feature.effective_mode ? (
          <small>requested {feature.requested_mode}</small>
        ) : null}
        {feature.configuration_error ? <small className="v2-status__error">{feature.configuration_error}</small> : null}
      </td>
      <td>
        <ul className="v2-status__stages">
          {feature.stages.map((stage) => (
            <StageCell key={stage.stage} stage={stage} />
          ))}
        </ul>
      </td>
      <td>
        {feature.detail ? (
          <>
            <strong>{feature.detail.different_contract}</strong> different contract
            <small>
              {feature.detail.scored} of {feature.detail.observations} comparisons scored
            </small>
          </>
        ) : (
          <ActivityCell activity={feature.stages[0]?.activity ?? null} basis={feature.activity_basis} />
        )}
      </td>
    </tr>
  );
}

function RunCard({ run }: { run: Run }) {
  const progress = run.target_candidates ? Math.min(100, (run.enrolled_candidates / run.target_candidates) * 100) : 0;
  const report = run.latest_full_cohort_report;
  return (
    <article className="v2-status__run">
      <header>
        <div>
          <strong>{run.kind.replace(/_/g, ' ')}</strong>
          <small>
            {run.run_id} · started {formatDate(run.started_at, { placeholder: 'unknown' })} · {run.status}
          </small>
        </div>
        {run.code_hash_matches ? (
          <span className="v2-status__pill v2-status__pill--ok">source unchanged</span>
        ) : (
          <span className="v2-status__pill v2-status__pill--warn">source changed — needs a new challenger</span>
        )}
      </header>

      <div className="v2-status__progress" role="img" aria-label={`${run.enrolled_candidates} of ${run.target_candidates} candidates enrolled`}>
        <div style={{ width: `${progress}%` }} />
      </div>
      <p className="v2-status__progress-label">
        {run.enrolled_candidates} of {run.target_candidates} candidates enrolled
        {run.promotion_sample_floor ? ` · promotion needs ${run.promotion_sample_floor} evaluable observations` : ''}
      </p>

      {report ? (
        <>
          <p className="v2-status__report-line">
            Full-cohort report {report.report_id.slice(0, 12)} · {report.n_observed} observed ·{' '}
            {report.covers_whole_cohort ? 'covers the whole cohort' : 'does not cover the whole cohort'} · regimes:{' '}
            {report.paired_regimes.length ? report.paired_regimes.join(', ') : 'none known'}
          </p>
          <div className="v2-status__table-wrap">
            <table className="v2-status__gates">
              <thead>
                <tr>
                  <th>Gate</th>
                  <th>Evaluable</th>
                  <th>Passed / rejected</th>
                  <th>Gate value</th>
                  <th>Still needed for promotion</th>
                </tr>
              </thead>
              <tbody>
                {report.gates.map((gate) => (
                  <tr key={gate.stage}>
                    <td>{gate.stage}</td>
                    <td>
                      {gate.n_evaluable}
                      <small>{gate.n_missing_outcomes} missing outcomes</small>
                    </td>
                    <td>
                      {gate.n_passed} / {gate.n_rejected}
                    </td>
                    <td className={(gate.gate_value ?? 0) > 0 ? 'v2-status__positive' : 'v2-status__negative'}>
                      {formatCurrency(gate.gate_value)}
                      <small>
                        {formatCurrency(gate.losses_avoided)} avoided · {formatCurrency(gate.profits_sacrificed)} given up
                      </small>
                    </td>
                    <td>
                      {gate.blocking_reasons.length ? (
                        <ul>
                          {gate.blocking_reasons.map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      ) : (
                        <span className="v2-status__positive">all promotion conditions met</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.gates.length ? null : <p className="v2-status__muted">No gate has been evaluated in this report yet.</p>}
        </>
      ) : (
        <p className="v2-status__muted">
          No full-cohort report yet. Promotion reads a <code>--month all</code> report, never a single favourable month.
        </p>
      )}
    </article>
  );
}

function V2StatusPage() {
  const { payload, error } = usePolledResource(getV2Status, { errorMessage: 'Failed to load V2 rollout status.' });

  return (
    <main className="v2-status">
      <section className="v2-status__hero">
        <div>
          <p>V2 Rollout</p>
          <h1>What the new architecture is doing</h1>
          <span>
            Every V2 control stays in shadow until a counterfactual proof, bound to its current thresholds and source,
            says otherwise. This page reads that state; it changes nothing.
          </span>
        </div>
        {payload ? <small>As of {formatDate(payload.as_of, { placeholder: 'unknown' })}</small> : null}
      </section>

      {error ? <section className="v2-status__empty">{error}</section> : null}
      {!payload && !error ? <section className="v2-status__empty">Reading the current V2 modes and evidence…</section> : null}

      {payload ? (
        <>
          <section className="v2-status__summary">
            <article>
              <span>Enforcing</span>
              <b>{payload.mode_summary.enforcing}</b>
            </article>
            <article>
              <span>Shadow</span>
              <b>{payload.mode_summary.shadow}</b>
            </article>
            <article>
              <span>Disabled</span>
              <b>{payload.mode_summary.disabled}</b>
            </article>
            <article className={payload.mode_summary.downgraded ? 'v2-status__summary--warn' : ''}>
              <span>Downgraded</span>
              <b>{payload.mode_summary.downgraded}</b>
            </article>
          </section>

          {payload.mode_summary.downgraded > 0 ? (
            <section className="v2-status__notice">
              {payload.mode_summary.downgraded} control{payload.mode_summary.downgraded === 1 ? ' is' : 's are'} set to
              enforce but running in shadow: no promotion proof matches the current thresholds and source.
            </section>
          ) : null}
          {payload.mode_summary.misconfigured > 0 ? (
            <section className="v2-status__notice">
              {payload.mode_summary.misconfigured} control{payload.mode_summary.misconfigured === 1 ? '' : 's'} could not
              be read. The same value raises in the trading pipeline, so fix the variable before the next run.
            </section>
          ) : null}
          {payload.activity_available ? null : (
            <section className="v2-status__notice">
              No candidate lifecycle tables in this database, so shadow verdicts cannot be counted. The zeroes below are
              absent readings, not quiet gates.
            </section>
          )}

          <section className="v2-status__card">
            <header>
              <div>
                <p>Controls</p>
                <h2>Requested mode against effective mode</h2>
              </div>
              <span>Shadow activity over the last {payload.window_days} days</span>
            </header>
            <div className="v2-status__table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Feature</th>
                    <th>Effective</th>
                    <th>Promotion stages</th>
                    <th>Shadow verdicts</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.features.map((feature) => (
                    <FeatureRow key={feature.key} feature={feature} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="v2-status__card">
            <header>
              <div>
                <p>Observation only</p>
                <h2>Components with no enforcement path</h2>
              </div>
            </header>
            <div className="v2-status__shadow-grid">
              {payload.shadow_only_features.map((feature) => (
                <article key={feature.key}>
                  <strong>{feature.label}</strong>
                  <p>{feature.description}</p>
                  <p className="v2-status__muted">{feature.reason}</p>
                  <p>
                    {feature.activity
                      ? `${feature.activity.shadow_blocks} would block of ${feature.activity.observed_candidates} observed`
                      : 'No candidate stream recorded'}
                  </p>
                </article>
              ))}
            </div>
          </section>

          <section className="v2-status__card">
            <header>
              <div>
                <p>Evidence</p>
                <h2>Frozen-forward research</h2>
              </div>
              {payload.research.champion?.run_id ? (
                <span>Champion run {payload.research.champion.run_id.slice(0, 12)}</span>
              ) : (
                <span>No promoted champion</span>
              )}
            </header>
            {payload.research.available ? (
              <div className="v2-status__runs">
                {payload.research.runs.length ? (
                  payload.research.runs.map((run) => <RunCard key={run.run_id} run={run} />)
                ) : (
                  <p className="v2-status__muted">
                    No research run has been started. Until one is, no gate can accumulate the evidence promotion needs.
                  </p>
                )}
              </div>
            ) : (
              <p className="v2-status__muted">{payload.research.reason}</p>
            )}
          </section>

          <section className="v2-status__card">
            <header>
              <div>
                <p>Prerequisites</p>
                <h2>Inputs the rollout waits on</h2>
              </div>
            </header>
            <div className="v2-status__prerequisites">
              {payload.prerequisites.map((item) => (
                <article key={item.key} className={`v2-status__prerequisite v2-status__prerequisite--${item.status}`}>
                  <span>{item.label}</span>
                  <b>{item.value}</b>
                  <p>{item.detail}</p>
                </article>
              ))}
            </div>
          </section>
        </>
      ) : null}
    </main>
  );
}

export default V2StatusPage;
