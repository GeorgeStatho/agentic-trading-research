import { fetchPolledJson } from './api';
import { asUtcTimestamp } from './format';

/**
 * What the shell's status line reads: the per-component bot heartbeat from
 * `/api/dashboard-kpis`, and the worker's last update from `/api/script-status`.
 */

export type BotComponent = {
  name: string;
  state: string;
  recent_error?: boolean;
};

export type ScriptStatus = {
  state: string;
  updated_at: string;
};

export type PillTone = 'running' | 'paused' | 'error' | 'unknown';

export type ComponentPill = {
  text: string;
  tone: PillTone;
};

/** The worker reports every few seconds even while paused; silence past this means it is down. */
export const HEARTBEAT_TIMEOUT_MS = 90_000;

/** A worker status read, stamped with when it was fetched so staleness is judged against that. */
export type ScriptStatusRead = {
  status: ScriptStatus;
  checkedAt: number;
};

export async function loadScriptStatus(): Promise<ScriptStatusRead> {
  const status = await fetchPolledJson<ScriptStatus>('/api/script-status', 'worker status');
  return { status, checkedAt: Date.now() };
}

function hasError(component: BotComponent): boolean {
  return component.state === 'error' || Boolean(component.recent_error);
}

/** "News Collector" -> "News collector"; acronyms such as "EOD" and "DB" stay. */
function sentenceCase(name: string): string {
  return name
    .split(' ')
    .map((word, index) => (index === 0 || word === word.toUpperCase() ? word : word.toLowerCase()))
    .join(' ');
}

function toneFor(component: BotComponent): PillTone {
  if (hasError(component)) {
    return 'error';
  }
  switch (component.state) {
    case 'running':
    case 'starting':
      return 'running';
    case 'paused':
      return 'paused';
    default:
      return 'unknown';
  }
}

export function componentPills(components: readonly BotComponent[]): ComponentPill[] {
  return components.map((component) => ({
    text: `${sentenceCase(component.name)} ${component.state || 'unknown'}`,
    tone: toneFor(component),
  }));
}

export function errorSummary(components: readonly BotComponent[]): string {
  const count = components.filter(hasError).length;
  if (count === 0) {
    return 'No errors';
  }
  return count === 1 ? '1 error' : `${count} errors`;
}

/** True when the worker status could not be read, or its last report is too old. */
export function heartbeatMissing(script: ScriptStatus | null, readFailed: boolean, now: number): boolean {
  if (readFailed) {
    return true;
  }
  if (!script) {
    return false;
  }
  const reportedAt = new Date(asUtcTimestamp(script.updated_at)).getTime();
  return Number.isNaN(reportedAt) || now - reportedAt > HEARTBEAT_TIMEOUT_MS;
}
