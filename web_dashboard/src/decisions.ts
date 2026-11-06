import { fetchJson, fetchPolledJson } from './api';
import { asUtcTimestamp, formatToken, sentenceCase } from './format';
import type { CompanyDecisionsFeed } from './liveFeeds';
import { formatOccSymbol } from './occ';

/**
 * The Decisions page: every proposal from `/api/decision-autopsies`, with
 * its frozen evidence, the path it took through the pipeline, and what
 * happened next.
 */

type Article = { article_id: number; title: string; source: string; published_at: string; article_scope: string };
type Claim = { claim_type?: string; claim?: string; evidence_article_ids?: number[] };
type ExecutionMode = 'paper' | 'live' | 'unlinked';
type Gate = { allowed?: boolean; enforcement_mode?: string; policy_version?: string; reason_codes?: string[] };

export type V2Verdict = { stage: string; label: string; reason_code: string; shadow: boolean; occurred_at: string };

export type DecisionRecord = {
  id: number;
  symbol: string;
  company_name: string;
  decision_run_at: string;
  strategist: { decision: string; confidence: string; model: string; summary: string; thesis: string[]; risks: string[]; evidence_claims: Claim[] };
  manager: { decision: string; confidence: string; reason: string };
  evidence: Article[];
  constraints: {
    target_dte_bucket: string;
    selected_option_id: string;
    selected_option_symbol: string;
    selected_expiration_date: string;
    selected_strike_price: number | null;
    selected_option_source: string;
    selection_debug?: { selected_option_snapshot?: { spread_pct?: number | null; open_interest?: number | null } };
    entry_gate?: Gate & {
      evidence?: { citation_count?: number; independent_source_count?: number };
      technical?: { trend_state?: string; relative_strength_confirmation?: string; volume_confirmation?: string };
    };
    market_regime?: { state?: string };
    market_regime_gate?: Gate & { regime_state?: string };
  };
  outcome: { trade_executed: boolean; order_id: string; pnl_pct: number | null; pnl_updated_at: string; resolved_outcome_label: string };
  execution?: { record_id: number | null; mode: ExecutionMode; paper: boolean | null; status: string; option_symbol: string; underlying_symbol: string; quantity: number | null; side: string; submitted_at: string };
  review: { status: string; headline: string; what_proved_us_wrong: string };
  /** Null until the attribution runner has scored the decision. */
  /** Percent moves over the holding period and IV as a 0-1 rate; null until the position has an outcome row. */
  benchmark?: {
    underlying_move: number | null; spy_return: number | null; sector_return: number | null; residual_move: number | null
    iv_at_entry: number | null; iv_at_exit: number | null; return_1d_after_exit: number | null; return_3d_after_exit: number | null
  } | null;
  attribution?: { evidence_quality: string; evidence_source: string; direction_outcome: string; expression_outcome: string; primary_failure: string; horizon_returns: { [horizon: string]: number } } | null;
  /** Absent from the seeded demo payload, which records no candidate lifecycle. */
  v2_verdicts?: V2Verdict[];
  /** The contract a blocked call or put picked, priced as if bought; null when there was none. */
  counterfactual?: Counterfactual | null;
};

/** A backtest-priced trade that never happened: entry at the first quote, exit by the DTE rule. */
export type Counterfactual = {
  option_symbol: string;
  /** `no_fill`: no usable entry quote; `no_exit_quote`: no price after entry yet. */
  status: 'simulated_closed' | 'no_exit_quote' | 'no_fill';
  entry_at: string | null;
  entry_price: number | null;
  exit_at: string | null;
  exit_price: number | null;
  realized_pnl_pct: number | null;
  realized_pnl_dollars: number | null;
  pricing_source: string;
  exit_reason: 'take_profit' | 'stop_loss' | 'last_mark' | null;
  still_open: boolean;
  take_profit_pct: number;
  stop_loss_pct: number;
  dte_bucket: string;
  warnings: string[];
};

/** Every decision of the current New York trading day, counted by the server. */
export type DayFunnel = { market_day: string; evaluated: number; trade_candidates: number; call: number; put: number; executed: number };

export type DecisionsPayload = {
  as_of: string;
  count: number;
  records: DecisionRecord[];
  v2_verdicts_available?: boolean;
  /** Absent from the seeded demo payload. */
  today?: DayFunnel;
  /** The requested day's funnel; present only when the request named a day. */
  day?: DayFunnel;
};

/** The newest 100 decisions, or with `day` (YYYY-MM-DD, New York) up to 250 from that trading day. */
export function getDecisions(day?: string): Promise<DecisionsPayload> {
  return fetchPolledJson<DecisionsPayload>(decisionsUrl(day), 'decisions');
}

export function decisionsUrl(day?: string, format?: 'csv'): string {
  const params = new URLSearchParams(day ? { day, limit: '250' } : { limit: format ? '250' : '100' });
  if (format) params.set('format', format);
  return `/api/decision-autopsies?${params}`;
}

export async function getDemoDecisions(): Promise<DecisionsPayload> {
  const payload = await fetchJson<{ autopsy: DecisionsPayload }>('/api/demo-mode', 'demo mode');
  return payload.autopsy;
}

export type StopStage = 'strategist' | 'manager' | 'safeguards' | 'order';
export type StepState = 'passed' | 'stopped' | 'also' | 'not_reached';
export type PathStep = { name: string; state: StepState; detail: string };

/** Rejections that only follow from the manager saying no; they are not safeguards. */
const CONSEQUENCE_CODES = new Set(['not_applicable', 'manager_not_directional', 'non_directional_decision', 'ineligible_recommendation']);

function isDirectional(decision: string): boolean {
  return decision === 'call' || decision === 'put';
}

/** Guarded features carry a name ("Liquidity V2 (E2)"); pipeline stages only their code ("EXECUTION"). */
export function verdictLabel(verdict: V2Verdict): string {
  return verdict.label === verdict.stage ? sentenceCase(formatToken(verdict.stage).toLowerCase()) : verdict.label;
}

export function safeguardVerdicts(record: DecisionRecord): V2Verdict[] {
  return (record.v2_verdicts ?? []).filter((verdict) => !CONSEQUENCE_CODES.has(verdict.reason_code));
}

function articleCount(record: DecisionRecord): string {
  const n = record.evidence.length;
  if (n === 0) return 'No articles cited';
  return `${n} article${n === 1 ? '' : 's'} cited`;
}

/** The safeguard that actually stopped the order, if one did; shadow verdicts only record. */
function enforcedVerdict(record: DecisionRecord): V2Verdict | undefined {
  return safeguardVerdicts(record).find((verdict) => !verdict.shadow);
}

function safeguardStep(record: DecisionRecord, reached: boolean): PathStep {
  const verdicts = safeguardVerdicts(record);
  const enforced = enforcedVerdict(record);
  if (reached && enforced) {
    return { name: 'Safeguards', state: 'stopped', detail: formatToken(enforced.reason_code) };
  }
  if (reached) {
    const detail = verdicts.length ? `${verdicts.length} would have blocked` : 'All passed';
    return { name: 'Safeguards', state: 'passed', detail };
  }
  const labels = [...new Set(verdicts.map(verdictLabel))];
  if (labels.length === 0) {
    return { name: 'Safeguards', state: 'not_reached', detail: 'Not reached' };
  }
  const more = labels.length > 1 ? ` +${labels.length - 1} more` : '';
  return { name: 'Safeguards', state: 'also', detail: `${labels[0]}${more}` };
}

function orderStep(record: DecisionRecord, reached: boolean): PathStep {
  const contract = record.execution?.option_symbol || record.constraints.selected_option_symbol;
  if (record.outcome.trade_executed) {
    return { name: 'Order', state: 'passed', detail: contract ? formatOccSymbol(contract) : 'Executed' };
  }
  if (reached) {
    return { name: 'Order', state: 'stopped', detail: 'Not filled' };
  }
  return { name: 'Order', state: 'not_reached', detail: contract ? 'Not submitted' : 'No contract chosen' };
}

/** News → Strategist → Manager → Safeguards → Order, and how far the decision got. */
export function decisionPath(record: DecisionRecord): PathStep[] {
  const strategistPassed = record.strategist.decision === 'trade_candidate';
  const managerPassed = strategistPassed && isDirectional(record.manager.decision);

  let manager: PathStep;
  if (!strategistPassed) {
    manager = { name: 'Manager', state: 'not_reached', detail: 'Not asked' };
  } else {
    manager = { name: 'Manager', state: managerPassed ? 'passed' : 'stopped', detail: formatToken(record.manager.decision, { placeholder: 'No trade' }) };
  }
  const safeguards = safeguardStep(record, managerPassed);

  return [
    { name: 'News', state: 'passed', detail: articleCount(record) },
    { name: 'Strategist', state: strategistPassed ? 'passed' : 'stopped', detail: formatToken(record.strategist.decision, { placeholder: 'No decision' }) },
    manager,
    safeguards,
    orderStep(record, safeguards.state === 'passed'),
  ];
}

export function stoppedAt(record: DecisionRecord): StopStage | null {
  const stopped = decisionPath(record).find((step) => step.state === 'stopped');
  return stopped ? (stopped.name.toLowerCase() as StopStage) : null;
}

/** One line for a list row: what stopped it, or the manager's reason. */
export function whyLine(record: DecisionRecord): string {
  const stage = stoppedAt(record);
  if (stage === 'strategist') {
    return `Strategist chose ${formatToken(record.strategist.decision).toLowerCase()} · ${record.strategist.confidence || 'unrated'} confidence`;
  }
  if (stage === 'safeguards') {
    const enforced = enforcedVerdict(record) as V2Verdict;
    return `Blocked by ${verdictLabel(enforced)}: ${formatToken(enforced.reason_code)}`;
  }
  return record.manager.reason;
}

/** How far decisions got: evaluated → trade candidate → call or put → executed. */
export type DecisionCounts = { evaluated: number; tradeCandidates: number; call: number; put: number; executed: number };

/** The funnel over the loaded records only. */
export function decisionCounts(records: readonly DecisionRecord[]): DecisionCounts {
  const count = (test: (record: DecisionRecord) => boolean) => records.filter(test).length;
  return {
    evaluated: records.length,
    tradeCandidates: count((record) => record.strategist.decision === 'trade_candidate'),
    call: count((record) => record.manager.decision === 'call'),
    put: count((record) => record.manager.decision === 'put'),
    executed: count((record) => record.outcome.trade_executed),
  };
}

/** The server's funnel over the whole trading day, which can hold more decisions than the page loads. */
export function funnelCounts(today: DayFunnel): DecisionCounts {
  return { evaluated: today.evaluated, tradeCandidates: today.trade_candidates, call: today.call, put: today.put, executed: today.executed };
}

export type VerdictFilter = 'all' | 'call' | 'put' | 'neither';
export type WhenFilter = 'today' | 'day' | 'all';
/** `day` (YYYY-MM-DD, New York) applies when `when` is `'day'`. */
export type DecisionFilters = { query: string; verdict: VerdictFilter; stoppedAt: StopStage | 'any'; when: WhenFilter; day: string };

/** Every decision in the chosen window; the summary chips count these. */
export const NO_FILTERS: Omit<DecisionFilters, 'when' | 'day'> = { query: '', verdict: 'all', stoppedAt: 'any' };

function matchesVerdict(decision: string, verdict: VerdictFilter): boolean {
  switch (verdict) {
    case 'all':
      return true;
    case 'neither':
      return !isDirectional(decision);
    default:
      return decision === verdict;
  }
}

const marketDay = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' });

/** The New York trading day (YYYY-MM-DD) a timestamp falls on, or '' when it does not parse. */
export function marketDayOf(timestamp: string | number): string {
  const time = typeof timestamp === 'number' ? timestamp : Date.parse(asUtcTimestamp(timestamp));
  return Number.isNaN(time) ? '' : marketDay.format(time);
}

/** A YYYY-MM-DD day moved by whole calendar days. */
export function shiftDay(day: string, days: number): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function inWindow(timestamp: string, filters: DecisionFilters, now: number): boolean {
  if (filters.when === 'all') return true;
  return marketDayOf(timestamp) === (filters.when === 'today' ? marketDayOf(now) : filters.day);
}

/** `now` is the payload's `as_of`, so "today" is the server's trading day. */
export function filterDecisions(records: readonly DecisionRecord[], filters: DecisionFilters, now: number): DecisionRecord[] {
  const needle = filters.query.trim().toLowerCase();
  return records.filter((record) => {
    if (needle && ![record.symbol, record.company_name].some((field) => field.toLowerCase().includes(needle))) return false;
    if (!matchesVerdict(record.manager.decision, filters.verdict)) return false;
    if (filters.stoppedAt !== 'any' && stoppedAt(record) !== filters.stoppedAt) return false;
    return inWindow(record.decision_run_at, filters, now);
  });
}

/** Hours between an article's publication and the decision that cited it. */
export function articleAgeHours(publishedAt: string, decidedAt: string): number | null {
  const published = Date.parse(asUtcTimestamp(publishedAt));
  const decided = Date.parse(asUtcTimestamp(decidedAt));
  if (Number.isNaN(published) || Number.isNaN(decided)) return null;
  return (decided - published) / 3_600_000;
}

export type DiagnosticsFeed = CompanyDecisionsFeed & {
  companies: Array<CompanyDecisionsFeed['companies'][number] & {
    diagnostics?: {
      historical_bars?: { error?: string; feed?: string };
      strategist_validation?: { status?: string; error_message?: string; input_article_count?: number; created_at?: string };
    };
  }>;
};

export function getDiagnostics(): Promise<DiagnosticsFeed> {
  return fetchPolledJson<DiagnosticsFeed>('/api/company-decisions', 'company decisions');
}

/**
 * Failures that leave no decision record behind, so the list cannot show
 * them: a strategist reply that failed validation, or missing price history.
 */
export function diagnosticNotices(feed: DiagnosticsFeed): string[] {
  const notices: string[] = [];
  for (const company of feed.companies) {
    const validation = company.diagnostics?.strategist_validation;
    if (validation?.status === 'failed') {
      const n = validation.input_article_count ?? 0;
      notices.push(`${company.symbol}: latest strategist attempt failed (${n} article${n === 1 ? '' : 's'} supplied): ${validation.error_message || 'no message'}`);
    }
    const barsError = company.diagnostics?.historical_bars?.error;
    if (barsError) {
      notices.push(`${company.symbol}: price history unavailable: ${barsError}`);
    }
  }
  return notices;
}
