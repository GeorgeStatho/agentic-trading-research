import { useEffect, useState } from 'react'
import './App.css'
import AnalyzedCompanyNewsPage from './analyzedCompanyNews.tsx'
import CompanyDecisionsPage from './companyDecisions.tsx'
import DecisionAutopsyPage from './decisionAutopsy.tsx'
import DecisionExpectancyPage from './decisionExpectancy.tsx'
import DashboardKpis from './dashboardKpis.tsx'
import ExposureMapPage from './exposureMap.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import RiskControlsPanel from './riskControls.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'

type DashboardView = 'dashboard' | 'company-news' | 'company-decisions' | 'decision-autopsy' | 'decision-expectancy' | 'exposure-map'

function getViewFromHash(): DashboardView {
  const normalizedHash = window.location.hash.replace(/^#\/?/, '').trim().toLowerCase().split('?')[0]
  if (normalizedHash === 'company-news') {
    return 'company-news'
  }
  if (normalizedHash === 'company-decisions') {
    return 'company-decisions'
  }
  if (normalizedHash === 'decision-autopsy') {
    return 'decision-autopsy'
  }
  if (normalizedHash === 'decision-expectancy') {
    return 'decision-expectancy'
  }
  if (normalizedHash === 'exposure-map') {
    return 'exposure-map'
  }
  return 'dashboard'
}

function getDemoModeFromHash(): boolean {
  return new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('demo') === '1'
}

function App() {
  const [view, setView] = useState<DashboardView>(() => getViewFromHash())
  const [demoMode, setDemoMode] = useState(() => getDemoModeFromHash())

  useEffect(() => {
    const handleHashChange = () => {
      setView(getViewFromHash())
      setDemoMode(getDemoModeFromHash())
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => {
      window.removeEventListener('hashchange', handleHashChange)
    }
  }, [])

  const hrefFor = (target: DashboardView) => `#/${target}${demoMode ? '?demo=1' : ''}`
  const toggleDemoMode = () => {
    window.location.hash = demoMode ? '#/exposure-map' : '#/exposure-map?demo=1'
  }

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <div className="app-brand">
          <p className="app-brand__eyebrow">Stock Trading Experiment</p>
          <h1>
            {view === 'dashboard'
              ? 'Operations Dashboard'
              : view === 'company-news'
                ? 'Analyzed Company News'
              : view === 'company-decisions'
                ? 'Company Decisions'
                : view === 'decision-autopsy'
                  ? 'Decision Autopsy Ledger'
                  : view === 'decision-expectancy'
                    ? 'Decision Expectancy'
                    : 'Evidence Exposure Map'}
          </h1>
        </div>
        <nav className="app-nav" aria-label="Dashboard pages">
          <a
            href={hrefFor('dashboard')}
            className={`app-nav__link${view === 'dashboard' ? ' app-nav__link--active' : ''}`}
          >
            Overview
          </a>
          <a
            href={hrefFor('company-news')}
            className={`app-nav__link${view === 'company-news' ? ' app-nav__link--active' : ''}`}
          >
            Company News
          </a>
          <a
            href={hrefFor('company-decisions')}
            className={`app-nav__link${view === 'company-decisions' ? ' app-nav__link--active' : ''}`}
          >
            Decisions
          </a>
          <a
            href={hrefFor('decision-autopsy')}
            className={`app-nav__link${view === 'decision-autopsy' ? ' app-nav__link--active' : ''}`}
          >
            Autopsy Ledger
          </a>
          <a
            href={hrefFor('decision-expectancy')}
            className={`app-nav__link${view === 'decision-expectancy' ? ' app-nav__link--active' : ''}`}
          >
            Expectancy
          </a>
          <a
            href={hrefFor('exposure-map')}
            className={`app-nav__link${view === 'exposure-map' ? ' app-nav__link--active' : ''}`}
          >
            Exposure Map
          </a>
          <button type="button" className={`app-nav__demo${demoMode ? ' app-nav__demo--active' : ''}`} onClick={toggleDemoMode}>
            {demoMode ? 'Exit Demo' : 'Demo Mode'}
          </button>
        </nav>
      </header>

      {view === 'company-news' ? (
        <AnalyzedCompanyNewsPage />
      ) : view === 'company-decisions' ? (
        <CompanyDecisionsPage />
      ) : view === 'decision-autopsy' ? (
        <DecisionAutopsyPage demoMode={demoMode} />
      ) : view === 'decision-expectancy' ? (
        <DecisionExpectancyPage />
      ) : view === 'exposure-map' ? (
        <ExposureMapPage demoMode={demoMode} />
      ) : (
        <main className="mainbody">
          <DashboardKpis />
          <RiskControlsPanel />
          <ScriptStatusIndicator />
          <WhyBotTradedPanel />
          <Graph />
          <OpenPositionsTable />
          <OrderCardList />
        </main>
      )}
    </div>
  )
}

export default App
