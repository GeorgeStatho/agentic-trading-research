import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import './App.css'
import AnalyzedCompanyNewsPage from './analyzedCompanyNews.tsx'
import BacktestPage from './backtest.tsx'
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

type DashboardView = 'dashboard' | 'company-news' | 'company-decisions' | 'decision-autopsy' | 'decision-expectancy' | 'backtests' | 'exposure-map'
type DashboardSection = 'dashboard-top' | 'live-feed' | 'risk-management' | 'strategy-editor' | 'execution-logs' | 'support'

type IconName = 'bell' | 'settings' | 'user' | 'dashboard' | 'feed' | 'shield' | 'note' | 'history' | 'support'

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M10 21h4" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.12 2.12-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.04 1.56V20h-3v-.08A1.7 1.7 0 0 0 10.66 18a1.7 1.7 0 0 0-1.88.34l-.06.06L6.6 16.28l.06-.06A1.7 1.7 0 0 0 7 14.34 1.7 1.7 0 0 0 5.44 13.3H5v-3h.44A1.7 1.7 0 0 0 7 9.26a1.7 1.7 0 0 0-.34-1.88l-.06-.06L8.72 5.2l.06.06A1.7 1.7 0 0 0 10.66 5a1.7 1.7 0 0 0 1.04-1.56V3h3v.44A1.7 1.7 0 0 0 15.74 5a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.12 2.12-.06.06A1.7 1.7 0 0 0 19.4 8.66 1.7 1.7 0 0 0 20.96 9.7H21v3h-.04A1.7 1.7 0 0 0 19.4 15Z" /></>,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c.8-4.1 3.5-6 8-6s7.2 1.9 8 6" /></>,
    dashboard: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    feed: <><path d="M4 11a9 9 0 0 1 9 9" /><path d="M4 4a16 16 0 0 1 16 16" /><circle cx="5" cy="19" r="1" /></>,
    shield: <path d="M12 3 20 6v5c0 5.3-3.4 8.9-8 10-4.6-1.1-8-4.7-8-10V6l8-3Z" />,
    note: <><path d="M5 3h12l3 3v15H5z" /><path d="M9 11h6M9 15h6M9 7h2" /></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5M12 7v5l3 2" /></>,
    support: <><path d="M4 14v-2a8 8 0 0 1 16 0v2" /><path d="M4 14h3v5H5a1 1 0 0 1-1-1v-4ZM20 14h-3v5h2a1 1 0 0 0 1-1v-4Z" /><path d="M17 19c0 2-1.8 2-5 2" /></>,
  }

  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>
}

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
  if (normalizedHash === 'backtests') {
    return 'backtests'
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
  const [activeDashboardSection, setActiveDashboardSection] = useState<DashboardSection>('dashboard-top')

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
  const scrollToDashboardSection = (section: DashboardSection) => {
    setActiveDashboardSection(section)
    document.getElementById(section)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const sidebarItems: Array<{ section: DashboardSection; label: string; icon: IconName }> = [
    { section: 'dashboard-top', label: 'Dashboard', icon: 'dashboard' },
    { section: 'live-feed', label: 'Live Feed', icon: 'feed' },
    { section: 'risk-management', label: 'Risk Management', icon: 'shield' },
    { section: 'strategy-editor', label: 'Strategy Editor', icon: 'note' },
    { section: 'execution-logs', label: 'Execution Logs', icon: 'history' },
    { section: 'support', label: 'Support', icon: 'support' },
  ]

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <div className="app-topbar__inner">
          <div className="app-brand"><h1>Agentic Trading Research</h1></div>
          <nav className="app-nav" aria-label="Dashboard pages">
            <a href={hrefFor('dashboard')} className={`app-nav__link${view === 'dashboard' ? ' app-nav__link--active' : ''}`}>Overview</a>
            <a href={hrefFor('company-news')} className={`app-nav__link${view === 'company-news' ? ' app-nav__link--active' : ''}`}>Company News</a>
            <a href={hrefFor('company-decisions')} className={`app-nav__link${view === 'company-decisions' ? ' app-nav__link--active' : ''}`}>Decisions</a>
            <a href={hrefFor('decision-autopsy')} className={`app-nav__link${view === 'decision-autopsy' ? ' app-nav__link--active' : ''}`}>Autopsy Ledger</a>
            <a href={hrefFor('decision-expectancy')} className={`app-nav__link${view === 'decision-expectancy' ? ' app-nav__link--active' : ''}`}>Expectancy</a>
            <a href={hrefFor('backtests')} className={`app-nav__link${view === 'backtests' ? ' app-nav__link--active' : ''}`}>Backtests</a>
            <a href={hrefFor('exposure-map')} className={`app-nav__link${view === 'exposure-map' ? ' app-nav__link--active' : ''}`}>Exposure Map</a>
            <button type="button" className={`app-nav__demo${demoMode ? ' app-nav__demo--active' : ''}`} onClick={toggleDemoMode}>{demoMode ? 'Exit Demo' : 'Demo Mode'}</button>
          </nav>
          <div className="app-topbar__actions" aria-label="Dashboard utilities">
            <button type="button" className="app-topbar__icon" aria-label="Notifications"><Icon name="bell" /></button>
            <button type="button" className="app-topbar__icon" aria-label="Settings"><Icon name="settings" /></button>
            <button type="button" className="app-topbar__icon" aria-label="Account"><Icon name="user" /></button>
          </div>
        </div>
      </header>

      <div className="app-layout">
        {view === 'dashboard' ? (
          <aside className="app-sidebar" aria-label="Dashboard sections">
            <div className="app-sidebar__status">
              <span className="app-sidebar__status-dot" />
              <div><p className="app-sidebar__status-label">System Active</p><p className="app-sidebar__latency">Latency: 12ms</p></div>
            </div>
            <nav className="app-sidebar__nav">
              {sidebarItems.map((item) => (
                <button
                  key={item.section}
                  type="button"
                  className={`app-sidebar__item${activeDashboardSection === item.section ? ' app-sidebar__item--active' : ''}`}
                  onClick={() => scrollToDashboardSection(item.section)}
                  aria-current={activeDashboardSection === item.section ? 'page' : undefined}
                >
                  <span className="app-sidebar__icon"><Icon name={item.icon} /></span>{item.label}
                </button>
              ))}
            </nav>
          </aside>
        ) : null}

      {view === 'company-news' ? (
        <AnalyzedCompanyNewsPage />
      ) : view === 'company-decisions' ? (
        <CompanyDecisionsPage />
      ) : view === 'decision-autopsy' ? (
        <DecisionAutopsyPage demoMode={demoMode} />
      ) : view === 'decision-expectancy' ? (
        <DecisionExpectancyPage />
      ) : view === 'backtests' ? (
        <BacktestPage />
      ) : view === 'exposure-map' ? (
        <ExposureMapPage demoMode={demoMode} />
      ) : (
        <main className="mainbody">
          <section id="dashboard-top" className="dashboard-hero dashboard-section">
            <div>
              <p className="dashboard-hero__eyebrow">Stock Trading Experiment</p>
              <h2>Operations Dashboard</h2>
            </div>
          </section>
          <DashboardKpis />
          <div id="risk-management" className="dashboard-section"><RiskControlsPanel /></div>
          <div id="live-feed" className="dashboard-section"><ScriptStatusIndicator /></div>
          <div id="strategy-editor" className="dashboard-section"><WhyBotTradedPanel /></div>
          <Graph />
          <div id="support" className="dashboard-section"><OpenPositionsTable /></div>
          <div id="execution-logs" className="dashboard-section"><OrderCardList /></div>
        </main>
      )}
      </div>
    </div>
  )
}

export default App
