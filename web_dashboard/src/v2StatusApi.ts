import { fetchPolledJson } from './api';

/**
 * The `/api/v2-status` reading, shared by the two panels that show it.
 *
 * Types and the fetch live apart from both so the Live safeguards strip and
 * the Research rollout view can each import them without importing each other.
 */

export type Activity = {
  observed_candidates: number;
  shadow_blocks: number;
  enforced_blocks: number;
};

export type Proof = {
  policy_hash: string;
  report_id: string;
  information_cutoff_time: string;
  sample_size: number;
  gate_value: number;
};

export type Stage = {
  stage: string;
  enforcement_allowed: boolean;
  proof: Proof | null;
  superseded_proofs: number;
  activity: Activity | null;
};

export type ScenarioDetail = {
  observations: number;
  scored: number;
  different_contract: number;
  basis: string;
};

export type Feature = {
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

export type ShadowOnlyFeature = {
  key: string;
  label: string;
  stage: string;
  description: string;
  reason: string;
  activity: Activity | null;
};

export type GateReadiness = {
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

export type CohortReport = {
  report_id: string;
  information_cutoff_time: string;
  n_observed: number | null;
  covers_whole_cohort: boolean;
  paired_regimes: string[];
  gates: GateReadiness[];
};

export type Run = {
  run_id: string;
  kind: string;
  status: string;
  started_at: string;
  target_candidates: number;
  enrolled_candidates: number;
  promotion_sample_floor: number | null;
  code_hash_matches: boolean;
  collection_version_matches?: boolean;
  latest_full_cohort_report: CohortReport | null;
};

export type Research = {
  available: boolean;
  reason?: string;
  runs: Run[];
  champion: { strategy: string; run_id: string | null; information_cutoff_time: string } | null;
};

export type Prerequisite = {
  key: string;
  label: string;
  status: string;
  value: string;
  detail: string;
};

export type ModeSummary = {
  enforcing: number;
  shadow: number;
  disabled: number;
  downgraded: number;
  misconfigured: number;
};

export type V2StatusPayload = {
  as_of: string;
  window_days: number;
  source_hash: string | null;
  mode_summary: ModeSummary;
  activity_available: boolean;
  features: Feature[];
  shadow_only_features: ShadowOnlyFeature[];
  research: Research;
  prerequisites: Prerequisite[];
};

export function getV2Status(): Promise<V2StatusPayload> {
  return fetchPolledJson<V2StatusPayload>('/api/v2-status', 'V2 rollout status');
}

/** The warnings the safeguard counts need, as sentences; shown on Live and on the rollout view. */
export function safeguardNotices(payload: V2StatusPayload): string[] {
  const { downgraded, misconfigured } = payload.mode_summary;
  const notices: string[] = [];
  if (downgraded > 0) {
    notices.push(
      `${downgraded} control${downgraded === 1 ? ' is' : 's are'} set to enforce but running in shadow: ` +
        'no promotion proof matches the current thresholds and source.',
    );
  }
  if (misconfigured > 0) {
    notices.push(
      `${misconfigured} control${misconfigured === 1 ? '' : 's'} could not be read. ` +
        'The same value raises in the trading pipeline, so fix the variable before the next run.',
    );
  }
  if (!payload.activity_available) {
    notices.push(
      'No candidate lifecycle tables in this database, so shadow verdicts cannot be counted. ' +
        'The zeroes are absent readings, not quiet gates.',
    );
  }
  return notices;
}
