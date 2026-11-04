import { useRef, useState } from 'react';
import type { ReactNode } from 'react';

import {
  articleAgeHours,
  decisionCounts,
  decisionPath,
  diagnosticNotices,
  filterDecisions,
  getDecisions,
  getDemoDecisions,
  getDiagnostics,
  NO_FILTERS,
  safeguardVerdicts,
  sentenceCase,
  verdictLabel,
  whyLine,
} from './decisions';
import type { DecisionFilters, DecisionRecord, DecisionsPayload, DiagnosticsFeed, PathStep, StepState, StopStage, VerdictFilter, WhenFilter } from './decisions';
import { formatDateTime, formatDteBucket, formatShortDateTime, formatSignedPercent, formatToken, formatTokenList } from './format';
import { formatOccSymbol } from './occ';
import PageHeader from './pageHeader';
import { searchFromHash } from './routes';
import { Collapse } from './ui/Collapse';
import { DataTable } from './ui/DataTable';
import type { Column } from './ui/DataTable';
import { Panel } from './ui/Panel';
import { SearchInput } from './ui/SearchInput';
import { Segmented } from './ui/Segmented';
import { Select } from './ui/Select';
import { Tag } from './ui/Tag';
import type { TagTone } from './ui/Tag';
import { TickerChip } from './ui/TickerChip';
import { usePolledResource } from './useRemoteResource';

/** The attribution runner's horizons, in trading days. */
const HORIZONS = ['5', '10'];

const VERDICT_OPTIONS: { value: VerdictFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'call', label: 'Calls' },
  { value: 'put', label: 'Puts' },
  { value: 'neither', label: 'No trade' },
];

const STOPPED_OPTIONS: { value: StopStage | 'any'; label: string }[] = [
  { value: 'any', label: 'Any stage' },
  { value: 'strategist', label: 'Strategist' },
  { value: 'manager', label: 'Manager' },
  { value: 'safeguards', label: 'Safeguard' },
  { value: 'order', label: 'Order' },
];

const WHEN_OPTIONS: { value: WhenFilter; label: string }[] = [
  { value: 'today', label: 'Today' },
  { value: 'all', label: 'All loaded' },
];

const STEP_STATE: Record<StepState, { label: string; className: string }> = {
  passed: { label: 'Passed', className: 'border-base-300 text-gain-text' },
  stopped: { label: '× Stopped here', className: 'border-loss bg-loss-bg text-loss-text' },
  also: { label: 'Also blocked', className: 'border-base-300 text-warning' },
  not_reached: { label: 'Not reached', className: 'border-dashed border-base-300 text-dim' },
};

const EXECUTION_MODE: Record<string, string> = {
  live: 'Live order',
  paper: 'Paper order',
  unlinked: 'Execution link unavailable',
};

function verdictTone(decision: string): TagTone {
  if (decision === 'trade_candidate') return 'gain';
  if (decision === 'call' || decision === 'put') return 'info';
  return 'neutral';
}

function signedTone(value: number): string {
  return value >= 0 ? 'text-gain-text' : 'text-loss-text';
}

function Label({ children }: { children: ReactNode }) {
  return <div className="text-[11px] text-dim">{children}</div>;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] text-dim">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function DecisionRow({ record, selected, onSelect }: { record: DecisionRecord; selected: boolean; onSelect: () => void }) {
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={`flex w-full flex-col gap-1.5 rounded-md border p-3 text-left ${selected ? 'border-base-300 bg-raised' : 'border-transparent hover:bg-raised/60'}`}
      >
        <span className="flex w-full items-center gap-2">
          <TickerChip symbol={record.symbol} />
          <span className="min-w-0 flex-1 truncate text-ink-2">{record.company_name}</span>
          <span className="font-mono text-[11px] text-dim">{formatShortDateTime(record.decision_run_at)}</span>
        </span>
        <span className="flex items-center gap-1.5 text-[11px] text-dim">
          S <Tag tone={verdictTone(record.strategist.decision)}>{formatToken(record.strategist.decision, { placeholder: '—' })}</Tag>
          <span aria-hidden="true">→</span>
          M <Tag tone={verdictTone(record.manager.decision)}>{formatToken(record.manager.decision, { placeholder: '—' })}</Tag>
        </span>
        <span className="line-clamp-1 text-xs text-ink-2">{whyLine(record)}</span>
      </button>
    </li>
  );
}

function DecisionPathSteps({ steps }: { steps: PathStep[] }) {
  return (
    <ol aria-label="Decision path" className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2">
      {steps.map((step) => {
        const state = STEP_STATE[step.state];
        return (
          <li key={step.name} className={`rounded-md border p-2.5 ${state.className}`}>
            <div className="text-[11px]">{state.label}</div>
            <div className="font-semibold text-base-content">{step.name}</div>
            <div className="text-xs text-ink-2">{step.detail}</div>
          </li>
        );
      })}
    </ol>
  );
}

function StrategistPanel({ record }: { record: DecisionRecord }) {
  const { strategist } = record;
  return (
    <Panel title="Strategist" meta={`${formatToken(strategist.decision, { placeholder: 'No decision' })} · ${strategist.confidence || 'unrated'}`} className="flex-[1_1_320px]">
      <div className="flex flex-col gap-3">
        {strategist.thesis.length ? (
          <ul className="list-disc space-y-1 pl-4">{strategist.thesis.map((item) => <li key={item}>{item}</li>)}</ul>
        ) : <p className="text-ink-2">No thesis was preserved.</p>}
        {strategist.risks.length ? (
          <div>
            <Label>Risks it flagged</Label>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-ink-2">{strategist.risks.map((risk) => <li key={risk}>{risk}</li>)}</ul>
          </div>
        ) : null}
        <Collapse title="Full strategist text">
          <p>{strategist.summary || 'No strategist summary was preserved.'}</p>
          {strategist.evidence_claims.length ? (
            <ul className="mt-2 space-y-1">
              {strategist.evidence_claims.map((claim, index) => (
                <li key={index}><span className="text-dim">{formatToken(claim.claim_type, { placeholder: 'Claim' })}:</span> {claim.claim}</li>
              ))}
            </ul>
          ) : null}
        </Collapse>
      </div>
    </Panel>
  );
}

function ManagerPanel({ record }: { record: DecisionRecord }) {
  const { manager, constraints } = record;
  const snapshot = constraints.selection_debug?.selected_option_snapshot;
  return (
    <Panel title="Manager" meta={`${formatToken(manager.decision, { placeholder: 'No trade' })} · ${manager.confidence || 'unrated'}`} className="flex-[1_1_320px]">
      <div className="flex flex-col gap-3">
        <p className="max-w-[80ch] leading-relaxed">{manager.reason || 'No manager reason was preserved.'}</p>
        {constraints.selected_option_symbol ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
            <Fact label="Contract"><span className="font-mono">{formatOccSymbol(constraints.selected_option_symbol)}</span></Fact>
            <Fact label="Target DTE">{formatDteBucket(constraints.target_dte_bucket)}</Fact>
            <Fact label="Spread">{typeof snapshot?.spread_pct === 'number' ? `${(snapshot.spread_pct * 100).toFixed(2)}%` : 'N/A'}</Fact>
            <Fact label="Open interest">{snapshot?.open_interest ?? 'N/A'}</Fact>
            <Fact label="Picked by">{formatToken(constraints.selected_option_source)}</Fact>
          </dl>
        ) : null}
      </div>
    </Panel>
  );
}

/** Monitoring only: the stock's move after a decision that did not trade. */
function WhatIfPanel({ record }: { record: DecisionRecord }) {
  const { attribution } = record;
  return (
    <Panel title="What if it had traded?" meta="monitoring only">
      <div className="flex flex-col gap-3">
        <div>
          <Label>{record.symbol} since the decision</Label>
          <div className="mt-1 flex flex-wrap gap-4">
            {HORIZONS.map((horizon) => {
              const move = attribution?.horizon_returns[horizon];
              return (
                <span key={horizon}>
                  <span className="text-dim">{horizon}d</span>{' '}
                  {typeof move === 'number'
                    ? <span className={`font-semibold ${signedTone(move)}`}>{formatSignedPercent(move * 100)}</span>
                    : <span className="text-ink-2">Pending</span>}
                </span>
              );
            })}
          </div>
        </div>
        {attribution ? (
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-dim">
            Evidence <Tag>{sentenceCase(formatToken(attribution.evidence_quality))}</Tag>
            Direction <Tag>{sentenceCase(formatToken(attribution.direction_outcome))}</Tag>
            Expression <Tag>{sentenceCase(formatToken(attribution.expression_outcome))}</Tag>
            <span>Primary failure: {formatToken(attribution.primary_failure)} · evidence from {formatToken(attribution.evidence_source)}</span>
          </div>
        ) : <p className="text-ink-2">Not scored by the attribution runner yet.</p>}
        {record.review.status !== 'not_executed' ? <p className="text-ink-2">{record.review.what_proved_us_wrong}</p> : null}
        <p className="text-[11px] text-dim">The hypothetical contract and its P/L under your exit rules are not recorded yet.</p>
      </div>
    </Panel>
  );
}

function OutcomePanel({ record }: { record: DecisionRecord }) {
  const { outcome, execution } = record;
  const contract = execution?.option_symbol || record.constraints.selected_option_symbol;
  return (
    <Panel title="Outcome" meta={EXECUTION_MODE[execution?.mode ?? 'unlinked']}>
      <div className="flex flex-col gap-3">
        <p className="font-semibold">{record.review.headline}</p>
        <dl className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-x-4 gap-y-2">
          <Fact label="P/L">
            {outcome.pnl_pct === null ? 'Pending' : <span className={signedTone(outcome.pnl_pct)}>{formatSignedPercent(outcome.pnl_pct)}</span>}
          </Fact>
          <Fact label="Outcome">{formatToken(outcome.resolved_outcome_label, { placeholder: 'Pending' })}</Fact>
          <Fact label="Contract"><span className="font-mono">{contract ? formatOccSymbol(contract) : 'Unavailable'}</span></Fact>
          <Fact label="Order"><span className="font-mono">{outcome.order_id || 'None'}</span></Fact>
          <Fact label="Status">{formatToken(execution?.status, { placeholder: 'Unavailable' })}</Fact>
          <Fact label="Submitted">{formatDateTime(execution?.submitted_at, { placeholder: 'Unavailable' })}</Fact>
        </dl>
        <div>
          <Label>What proved us wrong?</Label>
          <p className="text-ink-2">{record.review.what_proved_us_wrong}</p>
        </div>
      </div>
    </Panel>
  );
}

type GateRow = { key: string; gate: ReactNode; mode: string; result: string; tone: TagTone; why: string };

function gateResult(allowed: boolean | undefined): { result: string; tone: TagTone } {
  if (allowed === true) return { result: 'Allowed', tone: 'gain' };
  if (allowed === false) return { result: 'Blocked', tone: 'loss' };
  return { result: 'Not recorded', tone: 'neutral' };
}

function gateName(name: string, policy?: string): ReactNode {
  return (
    <>
      {name}
      {policy ? <div className="font-mono text-[11px] text-dim">{policy}</div> : null}
    </>
  );
}

function gateRows(record: DecisionRecord): GateRow[] {
  const entry = record.constraints.entry_gate;
  const regime = record.constraints.market_regime_gate;
  const rows: GateRow[] = [];
  if (entry) {
    const evidence = entry.evidence;
    const technical = entry.technical;
    const why = [
      evidence ? `${evidence.citation_count ?? 'N/A'} citations / ${evidence.independent_source_count ?? 'N/A'} sources` : '',
      technical?.trend_state ? `trend ${formatToken(technical.trend_state)}` : '',
      technical?.relative_strength_confirmation ? `RS ${formatToken(technical.relative_strength_confirmation)}` : '',
      technical?.volume_confirmation ? `volume ${formatToken(technical.volume_confirmation)}` : '',
      formatTokenList(entry.reason_codes),
    ].filter(Boolean).join(' · ');
    rows.push({ key: 'entry', gate: gateName('Evidence continuation', entry.policy_version), mode: sentenceCase(formatToken(entry.enforcement_mode, { placeholder: '—' })), ...gateResult(entry.allowed), why });
  }
  if (regime) {
    const state = regime.regime_state ?? record.constraints.market_regime?.state;
    const why = [state ? `${sentenceCase(formatToken(state))} regime` : '', formatTokenList(regime.reason_codes)].filter(Boolean).join(' · ');
    rows.push({ key: 'regime', gate: gateName('Market regime', regime.policy_version), mode: sentenceCase(formatToken(regime.enforcement_mode, { placeholder: '—' })), ...gateResult(regime.allowed), why });
  }
  for (const verdict of safeguardVerdicts(record)) {
    rows.push({
      key: `${verdict.stage}-${verdict.reason_code}`,
      gate: gateName(verdictLabel(verdict)),
      mode: verdict.shadow ? 'Shadow' : 'Enforced',
      result: verdict.shadow ? 'Would have blocked' : 'Blocked',
      tone: verdict.shadow ? 'neutral' : 'loss',
      why: formatToken(verdict.reason_code),
    });
  }
  return rows;
}

const GATE_COLUMNS: Column<GateRow>[] = [
  { key: 'gate', header: 'Gate', render: (row) => row.gate },
  { key: 'mode', header: 'Mode', render: (row) => row.mode },
  { key: 'result', header: 'Result', render: (row) => <Tag tone={row.tone}>{row.result}</Tag> },
  { key: 'why', header: 'Why', render: (row) => <span className="text-ink-2">{row.why}</span> },
];

function GatesPanel({ record, verdictsAvailable }: { record: DecisionRecord; verdictsAvailable: boolean }) {
  return (
    <Panel title="Gates" className="flex-[2_1_480px]" flush>
      <DataTable columns={GATE_COLUMNS} rows={gateRows(record)} rowKey={(row) => row.key} emptyMessage="No gate recorded a result." />
      {!verdictsAvailable ? (
        <p className="px-3.5 py-2 text-xs text-warning">V2 verdicts are not available for this ledger, so nothing here says whether a V2 gate objected.</p>
      ) : null}
    </Panel>
  );
}

function EvidencePanel({ record }: { record: DecisionRecord }) {
  return (
    <Panel title="Evidence cited" meta="age when the decision ran" className="flex-[1_1_320px]">
      {record.evidence.length ? (
        <ul className="flex flex-col divide-y divide-raised">
          {record.evidence.map((article) => {
            const age = articleAgeHours(article.published_at, record.decision_run_at);
            return (
              <li key={article.article_id} className="flex items-start justify-between gap-3 py-2 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <div>{article.title || `Article ${article.article_id}`}</div>
                  <div className="text-[11px] text-dim">{[article.source, formatShortDateTime(article.published_at, { placeholder: '' })].filter(Boolean).join(' · ')}</div>
                </div>
                {age !== null ? <span className="shrink-0 font-mono text-[11px] text-dim">{age.toFixed(1)} h old</span> : null}
              </li>
            );
          })}
        </ul>
      ) : <p className="text-ink-2">No article references were preserved for this decision.</p>}
    </Panel>
  );
}

function DecisionDetail({ record, verdictsAvailable }: { record: DecisionRecord; verdictsAvailable: boolean }) {
  const executed = record.outcome.trade_executed;
  return (
    <>
      <header className="flex flex-wrap items-start justify-between gap-3 rounded-box border border-base-300 bg-base-200 p-3.5">
        <div className="min-w-0">
          <h2 className="text-lg font-bold">{record.symbol} · {formatToken(record.manager.decision, { placeholder: 'No trade' })}</h2>
          <div className="text-ink-2">{[record.company_name, formatDateTime(record.decision_run_at), `snapshot #${record.id}`].filter(Boolean).join(' · ')}</div>
          {record.strategist.model ? <div className="font-mono text-[11px] text-dim">{record.strategist.model}</div> : null}
        </div>
        <div className="flex gap-1.5">
          <Tag>{sentenceCase(record.manager.confidence || 'unrated')} confidence</Tag>
          {executed ? <Tag tone="gain">Executed</Tag> : <Tag>Not executed</Tag>}
        </div>
      </header>
      <DecisionPathSteps steps={decisionPath(record)} />
      <div className="flex flex-wrap gap-3">
        <StrategistPanel record={record} />
        <ManagerPanel record={record} />
      </div>
      {executed ? <OutcomePanel record={record} /> : <WhatIfPanel record={record} />}
      <div className="flex flex-wrap gap-3">
        <GatesPanel record={record} verdictsAvailable={verdictsAvailable} />
        <EvidencePanel record={record} />
      </div>
    </>
  );
}

type DecisionsViewProps = {
  payload: DecisionsPayload;
  /** Failures that left no decision record behind (see `diagnosticNotices`). */
  notices: string[];
  /** A search handed over by a link, e.g. from Live. */
  initialQuery: string;
};

export function DecisionsView({ payload, notices, initialQuery }: DecisionsViewProps) {
  const now = Date.parse(payload.as_of);
  const [filters, setFilters] = useState<DecisionFilters>(() => {
    const ranToday = filterDecisions(payload.records, { ...NO_FILTERS, when: 'today' }, now).length > 0;
    return { query: initialQuery, verdict: 'all', stoppedAt: 'any', when: ranToday && !initialQuery ? 'today' : 'all' };
  });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const detailRef = useRef<HTMLElement>(null);

  const update = (change: Partial<DecisionFilters>) => setFilters((current) => ({ ...current, ...change }));
  const records = filterDecisions(payload.records, filters, now);
  const counts = decisionCounts(filterDecisions(payload.records, { ...NO_FILTERS, when: filters.when }, now));
  const selected = records.find((record) => record.id === selectedId) ?? records[0] ?? null;
  const newest = payload.records[0];

  function select(id: number) {
    setSelectedId(id);
    // On narrow screens the detail sits under the list; bring it into view.
    const list = listRef.current;
    const detail = detailRef.current;
    if (list && detail && detail.offsetTop > list.offsetTop + list.offsetHeight / 2) {
      detail.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
  }

  return (
    <main className="flex flex-col gap-3">
      <PageHeader
        title="Decisions"
        description="Every proposal with its frozen evidence and what happened next. Read-only."
        meta={newest ? `last run ${formatDateTime(newest.decision_run_at)} · newest ${payload.records.length} loaded` : undefined}
      />
      <div role="group" aria-label="Decision summary" className="flex flex-wrap gap-2 text-xs">
        {[
          `${counts.evaluated} evaluated`,
          `${counts.call} call`,
          `${counts.put} put`,
          `${counts.noTrade} no trade`,
          `${counts.executed} executed`,
        ].map((chip) => <span key={chip} className="rounded-sm bg-raised px-2 py-0.5 text-ink-2">{chip}</span>)}
      </div>
      {notices.length ? (
        <details className="text-xs text-warning">
          <summary className="cursor-pointer">{notices.length} latest {notices.length === 1 ? 'attempt' : 'attempts'} failed before reaching a decision</summary>
          <div className="mt-1 flex flex-col gap-0.5 pl-3">
            {notices.map((notice) => <p key={notice} className="text-xs text-warning">{notice}</p>)}
          </div>
        </details>
      ) : null}
      <div className="flex flex-wrap items-end gap-3">
        <SearchInput label="Search decisions" showLabel value={filters.query} onChange={(query) => update({ query })} placeholder="Ticker or company" />
        <div className="flex flex-col gap-1">
          <Label>Manager verdict</Label>
          <Segmented label="Manager verdict" options={VERDICT_OPTIONS} value={filters.verdict} onChange={(verdict) => update({ verdict })} />
        </div>
        <Select label="Stopped at" options={STOPPED_OPTIONS} value={filters.stoppedAt} onChange={(stopped) => update({ stoppedAt: stopped })} />
        <div className="flex flex-col gap-1">
          <Label>When</Label>
          <Segmented label="When" options={WHEN_OPTIONS} value={filters.when} onChange={(when) => update({ when })} />
        </div>
      </div>
      <div className="flex flex-wrap items-start gap-3">
        <div ref={listRef} className="min-w-0 flex-[1_1_320px]">
          <Panel title={`${records.length} decision${records.length === 1 ? '' : 's'}`} meta="newest first" flush>
            {records.length ? (
              <ul aria-label="Decisions" className="flex max-h-[70svh] flex-col gap-1 overflow-y-auto p-2">
                {records.map((record) => (
                  <DecisionRow key={record.id} record={record} selected={record.id === selected?.id} onSelect={() => select(record.id)} />
                ))}
              </ul>
            ) : <p className="p-3.5 text-dim">No decisions match these filters.</p>}
          </Panel>
        </div>
        <section ref={detailRef} aria-label="Selected decision" className="flex min-w-0 flex-[999_1_640px] flex-col gap-3">
          {selected ? <DecisionDetail record={selected} verdictsAvailable={payload.v2_verdicts_available !== false} /> : null}
        </section>
      </div>
    </main>
  );
}

function noDiagnostics(): Promise<DiagnosticsFeed> {
  return Promise.resolve({ last_updated_at: '', companies: [] });
}

function DecisionsPage({ demoMode = false }: { demoMode?: boolean }) {
  const decisions = usePolledResource(demoMode ? getDemoDecisions : getDecisions, { errorMessage: 'Failed to load decisions.' });
  const diagnostics = usePolledResource(demoMode ? noDiagnostics : getDiagnostics, { errorMessage: 'Failed to load company decisions.' });
  const [initialQuery] = useState(() => searchFromHash(window.location.hash));

  if (!decisions.payload) {
    return (
      <main className="flex flex-col gap-3">
        <PageHeader title="Decisions" description="Every proposal with its frozen evidence and what happened next. Read-only." />
        <p className={decisions.error ? 'text-error' : 'text-dim'}>{decisions.error ?? 'Loading decision records…'}</p>
      </main>
    );
  }

  const notices = diagnostics.payload ? diagnosticNotices(diagnostics.payload) : [];
  return (
    <>
      {demoMode ? <p className="mb-2 text-xs text-warning">Seeded fictional walkthrough. This review creates no trade, order, or live outcome.</p> : null}
      <DecisionsView payload={decisions.payload} notices={notices} initialQuery={initialQuery} />
    </>
  );
}

export default DecisionsPage;
