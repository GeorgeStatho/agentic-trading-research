import type { DecisionRecord } from '../decisions.ts'

type RecordOverrides = Omit<Partial<DecisionRecord>, 'strategist' | 'manager' | 'outcome' | 'constraints'> & {
  strategist?: Partial<DecisionRecord['strategist']>
  manager?: Partial<DecisionRecord['manager']>
  outcome?: Partial<DecisionRecord['outcome']>
  constraints?: Partial<DecisionRecord['constraints']>
}

/**
 * One `/api/decision-autopsies` record shaped like the live ones: the
 * strategist said watchlist, so the manager was never asked.
 */
export function decisionRecord(overrides: RecordOverrides = {}): DecisionRecord {
  const { strategist, manager, outcome, constraints, ...rest } = overrides
  return {
    id: 3281,
    symbol: 'DVN',
    company_name: 'Devon Energy Corporation',
    decision_run_at: '2026-11-04T16:27:24.546016+00:00',
    strategist: {
      decision: 'watchlist',
      confidence: 'medium',
      model: 'deepseek/deepseek-v4.1-flash-1017:nitro',
      summary: 'DVN has a constructive setup into Q3 earnings, but crude whipsaw leaves direction unclear.',
      thesis: ['Positive EPS revisions into earnings.', 'OPEC+ holding steady supports cash flow.'],
      risks: ['The Nov 5 earnings report is a binary event.'],
      evidence_claims: [],
      ...strategist,
    },
    manager: {
      decision: 'neither',
      confidence: 'low',
      reason: 'The strategist answered watchlist; the manager was not asked.',
      ...manager,
    },
    evidence: [
      { article_id: 15837, title: 'What Just Happened To Devon Energy Stock?', source: 'finance.yahoo.com', published_at: '2026-11-03T18:09:15Z', article_scope: 'company_news' },
    ],
    constraints: {
      target_dte_bucket: 'none',
      selected_option_id: '',
      selected_option_symbol: '',
      selected_expiration_date: '',
      selected_strike_price: null,
      selected_option_source: '',
      entry_gate: {
        allowed: false,
        enforcement_mode: 'disabled',
        policy_version: 'evidence-continuation-v2-provenance',
        reason_codes: ['policy_disabled'],
        evidence: { citation_count: 19, independent_source_count: 1 },
        technical: { trend_state: 'bearish', relative_strength_confirmation: 'weak', volume_confirmation: 'weak' },
      },
      market_regime_gate: {
        allowed: true,
        enforcement_mode: 'shadow',
        policy_version: 'market-regime-v3-scope',
        reason_codes: ['regime_gate_requires_directional_decision'],
        regime_state: 'range',
      },
      ...constraints,
    },
    outcome: {
      trade_executed: false,
      order_id: '',
      pnl_pct: null,
      pnl_updated_at: '',
      resolved_outcome_label: '',
      ...outcome,
    },
    execution: { record_id: null, mode: 'unlinked', paper: null, status: '', option_symbol: '', underlying_symbol: '', quantity: null, side: '', submitted_at: '' },
    review: {
      status: 'not_executed',
      headline: 'No order was executed.',
      what_proved_us_wrong: 'No live trade outcome exists; this proposal remains an evidence and constraint record only.',
    },
    attribution: {
      evidence_quality: 'weak',
      evidence_source: 'thesis_check',
      direction_outcome: 'abstained',
      expression_outcome: 'not_traded',
      primary_failure: 'none',
      horizon_returns: {},
    },
    benchmark: null,
    v2_verdicts: [
      { stage: 'MANAGER', label: 'MANAGER', reason_code: 'non_directional_decision', shadow: false, occurred_at: '2026-11-04T16:27:24Z' },
      { stage: 'ORDER_ELIGIBILITY', label: 'ORDER_ELIGIBILITY', reason_code: 'ineligible_recommendation', shadow: false, occurred_at: '2026-11-04T16:27:24Z' },
      { stage: 'LIQUIDITY', label: 'Liquidity V2 (E2)', reason_code: 'liquidity_wide_spread', shadow: true, occurred_at: '2026-11-04T16:27:24Z' },
      { stage: 'PROVENANCE', label: 'Provenance roots (P1)', reason_code: 'insufficient_independent_roots', shadow: true, occurred_at: '2026-11-04T15:33:30Z' },
    ],
    ...rest,
  }
}

/** The day's one call: the manager said call and the premium cap stopped the order. */
export function blockedCall(overrides: RecordOverrides = {}): DecisionRecord {
  return decisionRecord({
    id: 3260,
    symbol: 'MSFT',
    company_name: 'Microsoft Corporation',
    decision_run_at: '2026-11-04T15:52:37.891717+00:00',
    strategist: { decision: 'trade_candidate', confidence: 'medium' },
    manager: { decision: 'call', confidence: 'medium', reason: 'Azure +48% and a raised guide; not overextended. Size down.' },
    constraints: {
      target_dte_bucket: '30_45',
      selected_option_id: '93',
      selected_option_symbol: 'MSFT261218C00560000',
      selected_expiration_date: '2026-12-18',
      selected_strike_price: 560,
      selected_option_source: 'deterministic_medium_confidence_hybrid_fast_short_dte',
    },
    v2_verdicts: [
      { stage: 'EXECUTION', label: 'EXECUTION', reason_code: 'premium_per_trade_cap', shadow: false, occurred_at: '2026-11-04T15:52:38Z' },
      { stage: 'REGIME', label: 'Market regime scope (G3)', reason_code: 'regime_range_blocks_new_directional_entries', shadow: true, occurred_at: '2026-11-04T15:52:37Z' },
    ],
    ...overrides,
  })
}
