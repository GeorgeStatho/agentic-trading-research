import { formatShortTime } from './format';
import { componentPills, errorSummary } from './shellStatus';
import type { BotComponent, PillTone } from './shellStatus';

type StatusBarProps = {
  components: readonly BotComponent[] | null;
  lastRunAt: string | null;
};

const DOT_CLASS: Record<PillTone, string> = {
  running: 'bg-gain',
  paused: 'border border-dim',
  error: 'bg-loss',
  unknown: 'bg-raised',
};

/** One line under the top bar: each bot component's state, the last worker run, the error count. */
export function StatusBar({ components, lastRunAt }: StatusBarProps) {
  return (
    <div role="status" aria-label="System status" className="border-b border-base-300 bg-head">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-1 px-5 py-1.5 text-[11px] text-ink-2">
        {components ? componentPills(components).map((pill) => (
          <span key={pill.text} className="flex items-center gap-1.5">
            <span aria-hidden="true" className={`size-1.5 rounded-full ${DOT_CLASS[pill.tone]}`} />
            {pill.text}
          </span>
        )) : <span className="text-dim">Checking components…</span>}
        {lastRunAt ? <span className="text-dim">Last run {formatShortTime(lastRunAt)}</span> : null}
        {components ? <span className="text-dim">{errorSummary(components)}</span> : null}
      </div>
    </div>
  );
}
