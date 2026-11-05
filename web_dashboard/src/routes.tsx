import type { ReactNode } from 'react'
import AnalyzedCompanyNewsPage from './analyzedCompanyNews.tsx'
import BacktestPage from './backtest.tsx'
import DashboardHome from './dashboardHome.tsx'
import DecisionExpectancyPage from './decisionExpectancy.tsx'
import DecisionsPage from './decisionsPage.tsx'
import ExposureMapPage from './exposureMap.tsx'
import FollowerAccountsPage from './followerAccounts.tsx'
import NewsExtractionPage from './newsExtraction.tsx'
import NewsLagPage from './newsLag.tsx'
import SystemHealthPage from './systemHealth.tsx'
import SystemRiskPage from './systemRisk.tsx'
import V2StatusPage from './v2Status.tsx'

/**
 * The dashboard's pages, and the views inside each one.
 *
 * Three pages answer the three questions the eight before them did: what is
 * it doing now, why did it do that, and is the strategy worth keeping. A
 * fourth, System, holds the operational detail none of them is about. Routing, both navigation strips and demo mode all read this
 * table, so a view is added or moved in one place.
 */

export type Tab = {
  slug: string
  label: string
  /** Demo mode seeds a read-only walkthrough; only these views render one. */
  supportsDemo?: boolean
  render: (demoMode: boolean) => ReactNode
}

export type Route = {
  slug: string
  label: string
  /** Never empty. A single tab is a page in its own right and shows no strip. */
  tabs: readonly Tab[]
}

export type Location = {
  route: Route
  tab: Tab
}

export const ROUTES: readonly Route[] = [
  {
    slug: 'live',
    label: 'Live',
    tabs: [
      { slug: 'overview', label: 'Overview', render: () => <DashboardHome /> },
    ],
  },
  {
    slug: 'decisions',
    label: 'Decisions',
    tabs: [
      { slug: 'decisions', label: 'Decisions', supportsDemo: true, render: (demoMode) => <DecisionsPage demoMode={demoMode} /> },
      { slug: 'news', label: 'News', render: () => <AnalyzedCompanyNewsPage /> },
      { slug: 'map', label: 'Exposure map', supportsDemo: true, render: (demoMode) => <ExposureMapPage demoMode={demoMode} /> },
    ],
  },
  {
    slug: 'research',
    label: 'Research',
    tabs: [
      { slug: 'expectancy', label: 'Expectancy', render: () => <DecisionExpectancyPage /> },
      { slug: 'news-lag', label: 'News lag', render: () => <NewsLagPage /> },
      { slug: 'backtests', label: 'Backtests', render: () => <BacktestPage /> },
      { slug: 'followers', label: 'Followers', render: () => <FollowerAccountsPage /> },
      { slug: 'rollout', label: 'V2 Rollout', supportsDemo: true, render: (demoMode) => <V2StatusPage demoMode={demoMode} /> },
    ],
  },
  {
    slug: 'system',
    label: 'System',
    tabs: [
      { slug: 'health', label: 'Health', render: () => <SystemHealthPage /> },
      { slug: 'risk', label: 'Risk', render: () => <SystemRiskPage /> },
      { slug: 'extraction', label: 'Extraction', render: () => <NewsExtractionPage /> },
    ],
  },
]

const DEFAULT_ROUTE = ROUTES[0]

/** Where "Demo Mode" lands from a view that has no walkthrough of its own. */
const DEMO_ENTRY_PATH = 'decisions/map'

/**
 * Paths from when every view was its own page.
 *
 * They stay resolvable for good: links and bookmarks were handed out under
 * them, and the README still documents the demo walkthrough at one.
 */
const ALIASES: Readonly<Record<string, string>> = {
  'dashboard': 'live',
  'company-news': 'decisions/news',
  'company-decisions': 'decisions/decisions',
  'decision-autopsy': 'decisions/decisions',
  'decisions/ledger': 'decisions/decisions',
  'decisions/autopsy': 'decisions/decisions',
  'exposure-map': 'decisions/map',
  'decision-expectancy': 'research/expectancy',
  'backtests': 'research/backtests',
  'v2-rollout': 'research/rollout',
  'research/extraction': 'system/extraction',
}


function hashFor(path: string, demoMode: boolean): string {
  return `#/${path}${demoMode ? '?demo=1' : ''}`
}

/** A page's own address; it opens on that page's first view. */
export function routeHash(route: Route, demoMode: boolean): string {
  return hashFor(route.slug, demoMode)
}

export function tabHash(route: Route, tab: Tab, demoMode: boolean): string {
  return hashFor(`${route.slug}/${tab.slug}`, demoMode)
}

export function locationFromHash(hash: string): Location {
  const path = hash.replace(/^#\/?/, '').trim().toLowerCase().split('?')[0]
  const [routeSlug, tabSlug] = (ALIASES[path] ?? path).split('/')
  const route = ROUTES.find((entry) => entry.slug === routeSlug) ?? DEFAULT_ROUTE
  return { route, tab: route.tabs.find((entry) => entry.slug === tabSlug) ?? route.tabs[0] }
}

function hashParams(hash: string): URLSearchParams {
  return new URLSearchParams(hash.split('?')[1] ?? '')
}

export function demoModeFromHash(hash: string): boolean {
  return hashParams(hash).get('demo') === '1'
}

/** The search a link hands a page, e.g. Live's "search all history". */
export function searchFromHash(hash: string): string {
  return hashParams(hash).get('q') ?? ''
}

/**
 * Where the demo toggle sends the reader.
 *
 * Leaving demo mode keeps them where they are, and entering it only moves them
 * when this view has no walkthrough to show. The toggle used to jump to the
 * Exposure Map either way, including from the Exposure Map itself.
 */
export function demoToggleHash({ route, tab }: Location, demoMode: boolean): string {
  if (demoMode || tab.supportsDemo) {
    return tabHash(route, tab, !demoMode)
  }
  return hashFor(DEMO_ENTRY_PATH, true)
}
