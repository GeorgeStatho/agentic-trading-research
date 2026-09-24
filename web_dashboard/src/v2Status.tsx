import { formatCurrency, formatDate } from './format';
import { usePolledResource } from './useRemoteResource';
import { V2SafeguardCounts, V2SafeguardNotices } from './v2Safeguards.tsx';
import { getV2Status } from './v2StatusApi';
import type { Activity, Feature, Run, Stage, V2StatusPayload } from './v2StatusApi';
import './v2Status.css';

/**
 * What the V2 architecture is actually doing right now.
 *
 * The distinction this view exists for: asking for `enforce` does not make a
 * gate enforce. A control only takes effect once its stages carry a promotion
 * proof bound to the current thresholds and source, so every row shows the
 * requested mode, the effective mode, and the evidence still outstanding.
 */

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
  const { payload, error } = usePolledResource<V2StatusPayload>(getV2Status, { errorMessage: 'Failed to load V2 rollout status.' });

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
          <V2SafeguardCounts modes={payload.mode_summary} />
          <V2SafeguardNotices payload={payload} />

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
