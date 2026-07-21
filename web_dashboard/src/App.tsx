import { useEffect, useState } from 'react'
import './App.css'
import AnalyzedCompanyNewsPage from './analyzedCompanyNews.tsx'
import CompanyDecisionsPage from './companyDecisions.tsx'
import DecisionAutopsyPage from './decisionAutopsy.tsx'
import DashboardKpis from './dashboardKpis.tsx'
import ExposureMapPage from './exposureMap.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import RiskControlsPanel from './riskControls.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'

type DashboardView = 'dashboard' | 'company-news' | 'company-decisions' | 'decision-autopsy' | 'exposure-map'

function getViewFromHash(): DashboardView {
  const normalizedHash = window.location.hash.replace(/^#\/?/, '').trim().toLowerCase()
  if (normalizedHash === 'company-news') {
    return 'company-news'
  }
  if (normalizedHash === 'company-decisions') {
    return 'company-decisions'
  }
  if (normalizedHash === 'decision-autopsy') {
    return 'decision-autopsy'
  }
  if (normalizedHash === 'exposure-map') {
    return 'exposure-map'
  }
  return 'dashboard'
}

function App() {
  const [view, setView] = useState<DashboardView>(() => getViewFromHash())

  useEffect(() => {
    const handleHashChange = () => {
      setView(getViewFromHash())
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => {
      window.removeEventListener('hashchange', handleHashChange)
    }
  }, [])

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
                : 'Evidence Exposure Map'}
          </h1>
        </div>
        <nav className="app-nav" aria-label="Dashboard pages">
          <a
            href="#/dashboard"
            className={`app-nav__link${view === 'dashboard' ? ' app-nav__link--active' : ''}`}
          >
            Overview
          </a>
          <a
            href="#/company-news"
            className={`app-nav__link${view === 'company-news' ? ' app-nav__link--active' : ''}`}
          >
            Company News
          </a>
          <a
            href="#/company-decisions"
            className={`app-nav__link${view === 'company-decisions' ? ' app-nav__link--active' : ''}`}
          >
            Decisions
          </a>
          <a
            href="#/decision-autopsy"
            className={`app-nav__link${view === 'decision-autopsy' ? ' app-nav__link--active' : ''}`}
          >
            Autopsy Ledger
          </a>
          <a
            href="#/exposure-map"
            className={`app-nav__link${view === 'exposure-map' ? ' app-nav__link--active' : ''}`}
          >
            Exposure Map
          </a>
        </nav>
      </header>

      {view === 'company-news' ? (
        <AnalyzedCompanyNewsPage />
      ) : view === 'company-decisions' ? (
        <CompanyDecisionsPage />
      ) : view === 'decision-autopsy' ? (
        <DecisionAutopsyPage />
      ) : view === 'exposure-map' ? (
        <ExposureMapPage />
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
