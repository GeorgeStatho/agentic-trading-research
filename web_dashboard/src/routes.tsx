import type { ReactNode } from 'react'
import AnalyzedCompanyNewsPage from './analyzedCompanyNews.tsx'
import BacktestPage from './backtest.tsx'
import CompanyDecisionsPage from './companyDecisions.tsx'
import DashboardHome from './dashboardHome.tsx'
import { DASHBOARD_SECTIONS } from './dashboardSections.ts'
import type { DashboardSection } from './dashboardSections.ts'
import DecisionAutopsyPage from './decisionAutopsy.tsx'
import DecisionExpectancyPage from './decisionExpectancy.tsx'
import ExposureMapPage from './exposureMap.tsx'
import V2StatusPage from './v2Status.tsx'

/**
 * One row per top-level page.
 *
 * Routing, the nav strip, the sidebar and demo mode all read this table, so a
 * page is added or moved in one place rather than across parallel lists.
 */
export type Route = {
  slug: string
  label: string
  /** Demo mode seeds a read-only walkthrough; only these pages render one. */
  supportsDemo?: boolean
  /** Pages that navigate their own sections; the shell draws them a sidebar. */
  sections?: readonly DashboardSection[]
  render: (demoMode: boolean) => ReactNode
}

export const ROUTES: readonly Route[] = [
  { slug: 'dashboard', label: 'Overview', sections: DASHBOARD_SECTIONS, render: () => <DashboardHome /> },
  { slug: 'company-news', label: 'Company News', render: () => <AnalyzedCompanyNewsPage /> },
  { slug: 'company-decisions', label: 'Decisions', render: () => <CompanyDecisionsPage /> },
  { slug: 'decision-autopsy', label: 'Autopsy Ledger', supportsDemo: true, render: (demoMode) => <DecisionAutopsyPage demoMode={demoMode} /> },
  { slug: 'decision-expectancy', label: 'Expectancy', render: () => <DecisionExpectancyPage /> },
  { slug: 'backtests', label: 'Backtests', render: () => <BacktestPage /> },
  { slug: 'exposure-map', label: 'Exposure Map', supportsDemo: true, render: (demoMode) => <ExposureMapPage demoMode={demoMode} /> },
  { slug: 'v2-rollout', label: 'V2 Rollout', render: () => <V2StatusPage /> },
]

const DEFAULT_ROUTE = ROUTES[0]

/** Where "Demo Mode" lands from a page that has no walkthrough of its own. */
const DEMO_ENTRY_SLUG = 'exposure-map'

/** Shared so a page without sections does not rebuild the scroll observer. */
export const NO_SECTIONS: readonly DashboardSection[] = []

export function hashFor(slug: string, demoMode: boolean): string {
  return `#/${slug}${demoMode ? '?demo=1' : ''}`
}

export function routeFromHash(hash: string): Route {
  const slug = hash.replace(/^#\/?/, '').trim().toLowerCase().split('?')[0]
  return ROUTES.find((route) => route.slug === slug) ?? DEFAULT_ROUTE
}

export function demoModeFromHash(hash: string): boolean {
  return new URLSearchParams(hash.split('?')[1] ?? '').get('demo') === '1'
}

/**
 * Where the demo toggle sends the reader.
 *
 * Leaving demo mode keeps them where they are, and entering it only moves them
 * when this page has no walkthrough to show. The toggle used to jump to the
 * Exposure Map either way, including from the Exposure Map itself.
 */
export function demoToggleHash(route: Route, demoMode: boolean): string {
  const slug = demoMode || route.supportsDemo ? route.slug : DEMO_ENTRY_SLUG
  return hashFor(slug, !demoMode)
}
