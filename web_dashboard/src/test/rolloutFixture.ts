import type { ForwardEvaluationPayload } from '../forwardEvaluationApi.ts'
import type { Feature, V2StatusPayload } from '../v2StatusApi.ts'

function feature(key: string, label: string, requested: string, stages: [string, number, number][], extra: Partial<Feature> = {}): Feature {
  return {
    key, label, description: `${label} description`, env_var: `${key.toUpperCase()}_MODE`,
    requested_mode: requested, effective_mode: 'shadow', downgraded: requested === 'enforce', activity_basis: 'candidates',
    stages: stages.map(([stage, blocks, observed]) => ({
      stage, enforcement_allowed: false, proof: null, superseded_proofs: 0,
      activity: { observed_candidates: observed, shadow_blocks: blocks, enforced_blocks: 0 },
    })),
    detail: null,
    ...extra,
  }
}

/** Shaped like `/api/v2-status` on Nov 4, 2026: three controls downgraded from enforce. */
export function v2Payload(): V2StatusPayload {
  return {
    as_of: '2026-11-04T19:30:00+00:00',
    window_days: 30,
    source_hash: 'abc',
    mode_summary: { enforcing: 0, shadow: 8, disabled: 0, downgraded: 3, misconfigured: 0 },
    activity_available: true,
    features: [
      feature('provenance', 'Provenance', 'enforce', [['PROVENANCE', 2577, 2577]]),
      feature('freshness', 'Freshness', 'enforce', [['FRESHNESS', 2238, 2577]]),
      feature('continuation', 'Continuation', 'shadow', [['CONTINUATION', 0, 551]]),
      feature('regime', 'Regime', 'enforce', [['REGIME', 551, 551]]),
      feature('liquidity', 'Liquidity', 'shadow', [['LIQUIDITY', 524, 551], ['EXECUTION_LIQUIDITY', 0, 2]]),
      feature('scenario_selection', 'Scenario selection', 'shadow', [], {
        activity_basis: 'selection',
        stages: [{ stage: 'SCENARIO_SELECTION', enforcement_allowed: false, proof: null, superseded_proofs: 0, activity: null }],
        detail: { observations: 551, scored: 3, different_contract: 2, basis: 'scenario ranking' },
      }),
    ],
    shadow_only_features: [
      { key: 'price_absorption', label: 'Price absorption', stage: 'G1', description: 'How much was already absorbed', reason: 'needs R1 calibration', activity: { observed_candidates: 2577, shadow_blocks: 28, enforced_blocks: 0 } },
    ],
    research: { available: true, runs: [], champion: null },
    prerequisites: [
      { key: 'candidate_stream', label: 'Candidate stream', status: 'ok', value: '2577 in 30d', detail: 'Roughly 601 candidates a week.' },
      { key: 'outcome_resolution', label: 'Outcome resolution', status: 'ok', value: '31/37 closed', detail: '6 executed decisions have no closed outcome.' },
    ],
  }
}

function run(enrolled: number) {
  return { run_id: `run-${enrolled}`, status: 'open', enrolled, resolved: enrolled, target_candidates: 10000 }
}

export function forwardPayload(): ForwardEvaluationPayload {
  return {
    as_of: '2026-11-04T19:30:00+00:00',
    available: true,
    window_days: 30,
    settings: { forward_evaluation: { mode: 'shadow' }, challenger: { mode: 'off', strategist_model: '', manager_model: '' } },
    cohorts: [
      { cohort_id: 'c1', base_pipeline_version: 'v2:abcdef0123456789abcdef', challenger_id: null, window_start: '2026-11-04T17:07:18+00:00', window_end: '2027-01-03T17:07:18+00:00', acceptance_hash: 'h', runs: { production: run(66) } },
      { cohort_id: 'c2', base_pipeline_version: 'v2:abcdef0123456789abcdef', challenger_id: null, window_start: '2026-11-02T15:33:19+00:00', window_end: '2027-01-01T15:33:19+00:00', acceptance_hash: 'h', runs: { production: run(0) } },
    ],
    decision_observations: {},
    paired_evaluations: [],
    mark_sessions: [{ session_date: '2026-11-03', status: 'complete', summary: { stored: 8 } }],
    comparisons: [],
    discovery: {
      events_seen: 5946,
      admitted_union: 159,
      route_status_counts: { production: { admitted: 157, not_admitted: 1001, not_evaluated: 4788 }, discovery: { admitted: 2, not_admitted: 70, not_evaluated: 5874 } },
      admission_latency_seconds_median: { production: 25301, discovery: 534602 },
      outcomes_by_admission: {},
    },
  }
}
