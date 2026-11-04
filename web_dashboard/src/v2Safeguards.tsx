import type { ReactNode } from 'react';
import { getV2Status, safeguardNotices } from './v2StatusApi';
import type { ModeSummary, V2StatusPayload } from './v2StatusApi';
import { usePolledResource } from './useRemoteResource';
import './v2Safeguards.css';

/**
 * Which V2 controls are actually in force.
 *
 * Asking for `enforce` does not make a gate enforce: a control only takes
 * effect once its stages carry a promotion proof bound to the current
 * thresholds and source. That gap used to be visible only on a page of its
 * own, so the operations view could show a healthy bot while a gate the
 * operator had switched on was quietly doing nothing.
 */

/** The Research view holding the proofs behind these counts. */
export const ROLLOUT_HREF = '#/research/rollout';

const COUNTS = [
  { key: 'enforcing', label: 'Enforcing' },
  { key: 'shadow', label: 'Shadow' },
  { key: 'disabled', label: 'Disabled' },
  { key: 'downgraded', label: 'Downgraded' },
] as const satisfies readonly { key: keyof ModeSummary; label: string }[];

/** The four mode counts. Shared with the rollout view, which leads with them. */
export function V2SafeguardCounts({ modes }: { modes: ModeSummary }) {
  return (
    <div className="v2-safeguards__counts">
      {COUNTS.map(({ key, label }) => (
        <article
          key={key}
          className={`v2-safeguards__count${key === 'downgraded' && modes.downgraded > 0 ? ' v2-safeguards__count--warn' : ''}`}
        >
          <span>{label}</span>
          <b>{modes[key]}</b>
        </article>
      ))}
    </div>
  );
}

/** Everything that makes the counts above read differently than they look. */
export function V2SafeguardNotices({ payload }: { payload: V2StatusPayload }) {
  return (
    <>
      {safeguardNotices(payload).map((notice) => <p key={notice} className="v2-safeguards__notice">{notice}</p>)}
    </>
  );
}

function SafeguardsPanel({ children }: { children: ReactNode }) {
  return (
    <section className="v2-safeguards">
      <div className="v2-safeguards__header">
        <div>
          <p className="v2-safeguards__eyebrow">V2 Safeguards</p>
          <h2>What is actually enforcing</h2>
        </div>
        <a className="v2-safeguards__link" href={ROLLOUT_HREF}>
          See the evidence
        </a>
      </div>
      {children}
    </section>
  );
}

function V2SafeguardsPanel() {
  const { payload, error } = usePolledResource(getV2Status, {
    errorMessage: 'Failed to load V2 safeguard modes.',
  });

  if (error) {
    return <SafeguardsPanel><p className="v2-safeguards__empty">{error}</p></SafeguardsPanel>;
  }

  if (!payload) {
    return <SafeguardsPanel><p className="v2-safeguards__empty">Reading which controls are in force…</p></SafeguardsPanel>;
  }

  return (
    <SafeguardsPanel>
      <V2SafeguardCounts modes={payload.mode_summary} />
      <V2SafeguardNotices payload={payload} />
    </SafeguardsPanel>
  );
}

export default V2SafeguardsPanel;
