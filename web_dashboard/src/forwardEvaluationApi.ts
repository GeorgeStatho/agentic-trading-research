import { fetchJson, fetchPolledJson } from './api';

/**
 * The `/api/forward-evaluation` reading: forward cohorts, the paired challenger
 * comparison, collection health, and the discovery and research route ledgers.
 * Every record it describes is shadow research; none of it places an order.
 */

export type RunProgress = {
  run_id: string;
  status: string;
  enrolled: number;
  resolved: number;
  target_candidates: number;
};

export type Cohort = {
  cohort_id: string;
  base_pipeline_version: string;
  challenger_id: string | null;
  window_start: string;
  window_end: string;
  acceptance_hash: string;
  runs: Record<string, RunProgress>;
};

export type Criterion = {
  criterion: string;
  passed: boolean;
  value: unknown;
  threshold: unknown;
};

export type StrategySummary = {
  observations: number;
  trades: number;
  cash: number;
  net_pnl: number;
  capital_at_risk: number;
  net_return: number | null;
  win_rate: number | null;
  worst_trade: number | null;
  max_drawdown: number;
  verified_daily_marks: boolean;
};

export type Comparison = {
  report_id: string;
  cohort_id: string;
  information_cutoff_time: string;
  window: { start: string; end: string };
  challenger_definition: { strategist_model: string; manager_model: string; challenger_id: string } | null;
  verdict: 'interim' | 'accepted' | 'not_accepted';
  final: boolean;
  observations: { recorded: number; unavailable: number; pair_statuses: Record<string, number> };
  n_paired: number;
  n_clusters: number;
  regimes: string[];
  coverage: number | null;
  paired_difference: number;
  mean_paired_difference: number | null;
  bootstrap: { lower_bound: number | null; upper_bound: number | null; confidence?: number };
  incumbent: StrategySummary;
  challenger: StrategySummary;
  criteria: Criterion[];
  research_cost: { challenger_model_calls: number; challenger_latency_seconds_median: number | null };
};

export type EvaluationCount = { role: string; status: string; n: number; model_calls: number | null };

export type MarkSession = {
  session_date: string;
  status: string;
  summary: { stored?: number; symbols?: number; missing?: string[]; error?: string } | null;
};

export type HorizonOutcome = {
  events: number;
  resolved: number;
  mean_signed_residual_return: number | null;
  hit_rate: number | null;
  mean_absolute_residual_return: number | null;
};

export type DiscoveryLedger = {
  events_seen: number;
  admitted_union: number;
  route_status_counts: Record<string, Record<string, number>>;
  admission_latency_seconds_median: Record<string, number | null>;
  outcomes_by_admission: Record<string, Record<string, HorizonOutcome>>;
};

export type ThesisOutcome = {
  theses: number;
  resolved: number;
  mean_signed_residual_return: number | null;
  hit_rate: number | null;
};

export type ResearchRoute = {
  theses: number;
  outcomes_by_check_status: Record<string, ThesisOutcome>;
  research_cost: { reserved_cost_usd: number; model_and_retrieval_calls: number };
  latency_seconds: { session_to_thesis_median: number | null; event_to_thesis_median: number | null };
};

export type ForwardSettings = {
  forward_evaluation: { mode?: string; configuration_error?: string };
  challenger: {
    mode?: string;
    strategist_model?: string;
    manager_model?: string;
    daily_model_calls?: number;
    configuration_error?: string;
  };
};

export type ForwardEvaluationPayload = {
  as_of: string;
  available: boolean;
  reason?: string;
  demo_data?: boolean;
  window_days?: number;
  settings: ForwardSettings;
  cohorts?: Cohort[];
  decision_observations?: Record<string, number>;
  paired_evaluations?: EvaluationCount[];
  mark_sessions?: MarkSession[];
  comparisons?: Comparison[];
  discovery?: DiscoveryLedger;
  research_route?: ResearchRoute;
};

export function getForwardEvaluation(): Promise<ForwardEvaluationPayload> {
  return fetchPolledJson<ForwardEvaluationPayload>('/api/forward-evaluation', 'forward evaluation');
}

export async function getDemoForwardEvaluation(): Promise<ForwardEvaluationPayload> {
  const demo = await fetchJson<{ forward_evaluation: ForwardEvaluationPayload }>('/api/demo-mode', 'demo mode');
  return demo.forward_evaluation;
}
