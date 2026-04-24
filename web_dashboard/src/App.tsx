import { useEffect, useState } from 'react'
import './App.css'
import AnalyzedCompanyNewsPage from './analyzedCompanyNews.tsx'
import DashboardKpis from './dashboardKpis.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import RiskControlsPanel from './riskControls.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'

type DashboardView = 'dashboard' | 'company-news'

function getViewFromHash(): DashboardView {
  const normalizedHash = window.location.hash.replace(/^#\/?/, '').trim().toLowerCase()
  return normalizedHash === 'company-news' ? 'company-news' : 'dashboard'
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
          <h1>{view === 'dashboard' ? 'Operations Dashboard' : 'Analyzed Company News'}</h1>
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
        </nav>
      </header>

      {view === 'company-news' ? (
        <AnalyzedCompanyNewsPage />
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
