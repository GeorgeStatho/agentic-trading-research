import type { IconName } from './icon.tsx';

/**
 * The Overview page's sections, in the order they appear.
 *
 * The sidebar is generated from this list, so each `id` has to match the
 * element carrying it in `dashboardHome.tsx`. The sidebar used to keep its own
 * hand-written list, which named panels this page does not have ("Strategy
 * Editor", "Support") while leaving the KPI cards and the equity chart with no
 * anchor at all.
 */

export type DashboardSection = {
  id: string;
  label: string;
  icon: IconName;
};

export const DASHBOARD_SECTIONS: readonly DashboardSection[] = [
  // Live state first; static configuration last.
  { id: 'dashboard-top', label: 'Overview', icon: 'dashboard' },
  { id: 'account-snapshot', label: 'Account Snapshot', icon: 'chart' },
  { id: 'open-positions', label: 'Open Positions', icon: 'briefcase' },
  { id: 'live-feed', label: 'Main Script', icon: 'feed' },
  { id: 'decision-context', label: 'Why It Traded', icon: 'note' },
  { id: 'portfolio-history', label: 'Equity History', icon: 'trend' },
  { id: 'execution-logs', label: 'Executed Orders', icon: 'history' },
  { id: 'safeguards', label: 'V2 Safeguards', icon: 'lock' },
  { id: 'risk-management', label: 'Risk Settings', icon: 'shield' },
];
