import ForwardEvaluationPanel from './forwardEvaluation.tsx';
import { formatCurrency, formatDate, formatNumber, formatToken, sentenceCase } from './format';
import PageHeader from './pageHeader';
import type { Column } from './ui/DataTable';
import { DataTable } from './ui/DataTable';
import { Meter } from './ui/Meter';
import { Panel } from './ui/Panel';
import { StatStrip } from './ui/StatStrip';
import { Tag } from './ui/Tag';
import { usePolledResource } from './useRemoteResource';
import { NEAR_TOTAL_BLOCK_RATE, getV2Status, prerequisiteCount, safeguardNotices, wouldBlockRows } from './v2StatusApi';
import type { Feature, GateReadiness, Run, ShadowOnlyFeature, V2StatusPayload, WouldBlockRow } from './v2StatusApi';

/**
 * What the V2 architecture is actually doing right now.
 *
 * The distinction this view exists for: asking for `enforce` does not make a
 * gate enforce. A control only takes effect once its stages carry a promotion
 * proof bound to the current thresholds and source, so every row shows the
 * requested mode, the effective mode, and the evidence still outstanding.
 */

function count(value: number): string {
  return formatNumber(value, { digits: 0 });
}

function percent(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

function ModeTag({ mode }: { mode: string | null }) {
  return <Tag tone={mode === 'enforce' ? 'accent' : 'neutral'}>{sentenceCase(mode ?? 'unreadable')}</Tag>;
}

// ---- Would-block chart ------------------------------------------------------

function WouldBlockChart({ rows }: { rows: readonly WouldBlockRow[] }) {
  return <ul aria-label="Would-block rate by control" className="flex flex-col gap-2">
    {rows.map((row) => <li key={row.key} className="grid grid-cols-[minmax(7rem,12rem)_1fr_auto] items-center gap-2 text-[11px]">
      <span className="min-w-0">
        <span className="block truncate text-ink-2">{row.label}</span>
        <span className="block truncate font-mono text-[10px] text-dim">{row.envVar}</span>
      </span>
      <span className="h-3 rounded-sm bg-raised">
        <span className={`block h-full rounded-sm ${row.requestedEnforce ? 'bg-primary' : 'bg-[#6e6e76]'}`} style={{ width: `${row.rate * 100}%`, minWidth: row.blocks ? 2 : 0 }} />
      </span>
      <span className="whitespace-nowrap text-right"><span className="font-semibold">{percent(row.rate)}</span> <span className="text-dim">{count(row.blocks)} / {count(row.observed)}</span></span>
    </li>)}
  </ul>
}

function joinNames(names: readonly string[]): string {
  return names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** Said aloud under the chart: promoting these as they stand would stop nearly every trade. */
function nearTotalBlockNote(rows: readonly WouldBlockRow[]): string | null {
  const names = [...new Set(rows.filter((row) => row.requestedEnforce && row.rate >= NEAR_TOTAL_BLOCK_RATE).map((row) => row.label))];
  if (!names.length) return null;
  const subject = names.length === 1 ? 'it sees' : 'they see';
  return `${joinNames(names)} would block at least 95% of the candidates ${subject}. Enforcing ${names.length === 1 ? 'it' : 'any of them'} as-is would stop almost all trading.`;
}

// ---- Tables -----------------------------------------------------------------

function proofText(feature: Feature): string {
  return feature.stages.map((stage) => {
    const proof = stage.proof ? `${stage.proof.sample_size} obs, ${formatCurrency(stage.proof.gate_value)} gate value` : 'no proof';
    const superseded = stage.superseded_proofs ? ` (${stage.superseded_proofs} superseded)` : '';
    return feature.stages.length > 1 ? `${formatToken(stage.stage)}: ${proof}${superseded}` : `${proof}${superseded}`;
  }).join(' · ');
}

function wouldBlockText(feature: Feature) {
  if (feature.detail) {
    return <>{feature.detail.different_contract} different contract<span className="block text-[11px] text-dim">{feature.detail.scored} of {feature.detail.observations} scored</span></>;
  }
  const counted = feature.stages.filter((stage) => stage.activity);
  if (!counted.length) {
    return <span className="text-dim">{feature.activity_basis === 'positions' ? 'Recorded per position' : 'No candidate stream'}</span>;
  }
  return <>{counted.map((stage) => {
    const activity = stage.activity!;
    return <span key={stage.stage} className="block whitespace-nowrap">
      {count(activity.shadow_blocks)} / {count(activity.observed_candidates)}
      {activity.enforced_blocks ? <span className="text-dim"> · {activity.enforced_blocks} blocked</span> : null}
    </span>;
  })}</>;
}

const CONTROL_COLUMNS: Column<Feature>[] = [
  { key: 'control', header: 'Control', render: (feature) => <><span className="font-medium">{feature.label}</span><span className="block font-mono text-[10px] text-dim">{feature.env_var}</span></> },
  { key: 'what', header: 'What it does', render: (feature) => <span className="block max-w-80 whitespace-normal text-ink-2">{feature.description}</span> },
  { key: 'requested', header: 'Requested', render: (feature) => <ModeTag mode={feature.requested_mode} /> },
  { key: 'effective', header: 'Effective', render: (feature) => <>
    <ModeTag mode={feature.effective_mode} />
    {feature.configuration_error ? <span className="block text-[11px] text-error">{feature.configuration_error}</span> : null}
  </> },
  { key: 'proof', header: 'Proof', render: (feature) => <span className={feature.stages.some((stage) => stage.proof) ? '' : 'text-dim'}>{proofText(feature)}</span> },
  { key: 'would-block', header: 'Would block', align: 'right', render: wouldBlockText },
];

const SHADOW_ONLY_COLUMNS: Column<ShadowOnlyFeature>[] = [
  { key: 'component', header: 'Component', render: (feature) => <span className="font-medium">{feature.label}</span> },
  { key: 'what', header: 'What it does', render: (feature) => <span className="block max-w-96 whitespace-normal text-ink-2">{feature.description}</span> },
  { key: 'why', header: 'Why observe only', render: (feature) => <span className="block max-w-72 whitespace-normal text-dim">{feature.reason}</span> },
  { key: 'would-block', header: 'Would block', align: 'right', render: (feature) => (feature.activity
    ? <span className="whitespace-nowrap">{count(feature.activity.shadow_blocks)} / {count(feature.activity.observed_candidates)}</span>
    : <span className="text-dim">No stream</span>) },
];

const GATE_COLUMNS: Column<GateReadiness>[] = [
  { key: 'gate', header: 'Gate', render: (gate) => gate.stage },
  { key: 'evaluable', header: 'Evaluable', align: 'right', render: (gate) => <>{gate.n_evaluable}<span className="block text-[11px] text-dim">{gate.n_missing_outcomes} missing outcomes</span></> },
  { key: 'passed', header: 'Passed / rejected', align: 'right', render: (gate) => `${gate.n_passed} / ${gate.n_rejected}` },
  { key: 'value', header: 'Gate value', align: 'right', render: (gate) => <>
    <span className={(gate.gate_value ?? 0) > 0 ? 'text-gain' : 'text-loss'}>{formatCurrency(gate.gate_value)}</span>
    <span className="block text-[11px] text-dim">{formatCurrency(gate.losses_avoided)} avoided · {formatCurrency(gate.profits_sacrificed)} given up</span>
  </> },
  { key: 'needed', header: 'Still needed for promotion', render: (gate) => (gate.blocking_reasons.length
    ? <ul className="list-disc pl-4 whitespace-normal">{gate.blocking_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    : <span className="text-gain">all promotion conditions met</span>) },
];

function ResearchRun({ run }: { run: Run }) {
  const report = run.latest_full_cohort_report;
  const compatible = run.collection_version_matches ?? run.code_hash_matches;
  return <div className="flex flex-col gap-2 border-t border-base-300 px-3.5 py-3 first:border-t-0">
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-medium">{sentenceCase(run.kind.replace(/_/g, ' '))}</span>
      <span className="font-mono text-[11px] text-dim">{run.run_id}</span>
      <span className="text-[11px] text-dim">started {formatDate(run.started_at, { placeholder: 'unknown' })} · {run.status}</span>
      <Tag tone={compatible ? 'neutral' : 'loss'}>{compatible ? 'evaluation compatible' : 'evaluation changed, needs a new run'}</Tag>
    </div>
    <div className="max-w-md">
      <Meter label="Candidates enrolled" value={run.enrolled_candidates} max={run.target_candidates || 1} />
    </div>
    {run.promotion_sample_floor ? <p className="text-[11px] text-dim">Promotion needs {run.promotion_sample_floor} evaluable observations.</p> : null}
    {report ? <>
      <p className="text-[11px] text-dim">
        Full-cohort report {report.report_id.slice(0, 12)} · {report.n_observed} observed · {report.covers_whole_cohort ? 'covers the whole cohort' : 'does not cover the whole cohort'} · regimes: {report.paired_regimes.length ? report.paired_regimes.join(', ') : 'none known'}
      </p>
      <DataTable label={`Gates for ${run.run_id}`} columns={GATE_COLUMNS} rows={report.gates} rowKey={(gate) => gate.stage} emptyMessage="No gate has been evaluated in this report yet." />
    </> : <p className="text-[11px] text-dim">No full-cohort report yet. Promotion reads a <code className="font-mono">--month all</code> report, never a single favourable month.</p>}
  </div>
}

// ---- Page -------------------------------------------------------------------

function RolloutView({ payload }: { payload: V2StatusPayload }) {
  const modes = payload.mode_summary;
  const requestedEnforce = payload.features.filter((feature) => feature.requested_mode === 'enforce').length;
  const rows = wouldBlockRows(payload.features);
  const note = nearTotalBlockNote(rows);
  const stats = [
    { label: 'Enforcing', value: modes.enforcing, sub: `of ${payload.features.length} controls` },
    { label: 'Shadow', value: modes.shadow, sub: requestedEnforce ? `${requestedEnforce} requested enforce` : undefined },
    { label: 'Disabled', value: modes.disabled },
    { label: 'Downgraded', value: modes.downgraded, sub: modes.downgraded ? 'no proof matches thresholds' : undefined },
    ...(modes.misconfigured ? [{ label: 'Misconfigured', value: modes.misconfigured, tone: 'loss' as const }] : []),
  ];

  return <>
    <StatStrip items={stats} />
    {safeguardNotices(payload).map((notice) => <p key={notice} className="text-xs text-warning">{notice}</p>)}

    <div className="flex flex-wrap gap-3">
      <Panel
        className="min-w-72 flex-[3_1_30rem]"
        title="Would-block rate by control"
        meta={`shadow, last ${payload.window_days} days`}
        actions={<div className="flex gap-3 text-[11px] text-dim">
          <span className="flex items-center gap-1.5"><i className="size-2.5 rounded-sm bg-primary" />Requested enforce</span>
          <span className="flex items-center gap-1.5"><i className="size-2.5 rounded-sm bg-[#6e6e76]" />Shadow / observe</span>
        </div>}
      >
        {rows.length ? <WouldBlockChart rows={rows} /> : <p className="text-dim">No control has a candidate stream recorded.</p>}
        {note ? <p className="mt-3 text-xs text-warning">{note}</p> : null}
      </Panel>
      <Panel className="min-w-64 flex-[2_1_18rem]" title="What promotion is waiting on">
        <div className="flex flex-col gap-3">
          {payload.prerequisites.map((item) => {
            const counted = prerequisiteCount(item.value);
            return <div key={item.key} className="flex flex-col gap-1">
              {counted ? <Meter label={item.label} value={counted.value} max={counted.max} tone={item.status === 'ok' ? 'success' : 'primary'} /> : (
                <div className="flex justify-between gap-2 text-[11px]"><span className="text-dim">{item.label}</span><span className={item.status === 'ok' ? 'text-gain' : 'text-warning'}>{item.value}</span></div>
              )}
              <p className="text-[11px] text-dim">{item.detail}</p>
            </div>;
          })}
        </div>
      </Panel>
    </div>

    <Panel title="Controls" meta="requested vs. effective mode" flush>
      <DataTable label="Controls" columns={CONTROL_COLUMNS} rows={payload.features} rowKey={(feature) => feature.key} rowClassName={(feature) => (feature.downgraded ? 'bg-accent-bg/40' : '')} />
    </Panel>

    {payload.shadow_only_features.length ? <Panel title="Observation only" meta="components with no enforcement path" flush>
      <DataTable label="Observation only" columns={SHADOW_ONLY_COLUMNS} rows={payload.shadow_only_features} rowKey={(feature) => feature.key} />
    </Panel> : null}

    <Panel title="Frozen-forward research" meta={payload.research.champion?.run_id ? `champion run ${payload.research.champion.run_id.slice(0, 12)}` : 'no promoted champion'} flush>
      {!payload.research.available ? <p className="p-3.5 text-dim">{payload.research.reason}</p> : null}
      {payload.research.available && !payload.research.runs.length ? <p className="p-3.5 text-dim">No research run has been started. Until one is, no gate can accumulate the evidence promotion needs.</p> : null}
      {payload.research.runs.map((run) => <ResearchRun key={run.run_id} run={run} />)}
    </Panel>

    <ForwardEvaluationPanel />
  </>
}

function LiveV2StatusPage() {
  const { payload, error } = usePolledResource<V2StatusPayload>(getV2Status, { errorMessage: 'Failed to load V2 rollout status.' });

  return <main className="flex flex-col gap-3">
    <PageHeader
      title="V2 rollout"
      description="Every V2 control stays in shadow until a counterfactual proof, bound to its current thresholds and source, says otherwise. Read-only."
      meta={payload ? `As of ${formatDate(payload.as_of, { placeholder: 'unknown' })}` : null}
    />
    {error ? <p className="text-xs text-error">{error}</p> : null}
    {!payload && !error ? <p className="text-dim">Reading the current V2 modes and evidence…</p> : null}
    {payload ? <RolloutView payload={payload} /> : null}
  </main>
}

function V2StatusPage({ demoMode = false }: { demoMode?: boolean }) {
  if (!demoMode) return <LiveV2StatusPage />;
  return <main className="flex flex-col gap-3">
    <PageHeader
      title="Forward evaluation walkthrough"
      description="Demo: only the forward-evaluation panel has one; live controls and runs are not shown."
    />
    <p className="text-xs text-warning">Seeded fictional walkthrough. No cohort, challenger, comparison or order exists behind these numbers.</p>
    <ForwardEvaluationPanel demoMode />
  </main>
}

export default V2StatusPage;
