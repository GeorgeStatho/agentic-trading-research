import { fetchPolledJson } from './api';

/**
 * What the shell's top bar and status sub-bar read: the per-component bot
 * heartbeat and market clock from `/api/dashboard-kpis`, and the worker's
 * last update from `/api/script-status`.
 */

export type BotComponent = {
  name: string;
  state: string;
  recent_error?: boolean;
};

export type ShellKpis = {
  bot_status: {
    state: string;
    label: string;
    detail: string;
    components?: BotComponent[];
  };
  market_status: {
    state: string;
    label: string;
    detail: string;
  };
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

export function loadShellKpis(): Promise<ShellKpis> {
  return fetchPolledJson<ShellKpis>('/api/dashboard-kpis', 'system status');
}

export function loadScriptStatus(): Promise<ScriptStatus> {
  return fetchPolledJson<ScriptStatus>('/api/script-status', 'worker status');
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
