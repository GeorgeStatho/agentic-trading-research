import DashboardKpis from './dashboardKpis.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import PageHeader from './pageHeader.tsx'
import RiskControlsPanel from './riskControls.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
import V2SafeguardsPanel from './v2Safeguards.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'

/** The Overview page. Its anchors are listed in `dashboardSections.ts`. */
function DashboardHome() {
  return (
    <main className="mainbody">
      <PageHeader id="dashboard-top" className="dashboard-section" title="Operations" description="What the bot holds and is doing right now. Paper trading through Alpaca." />
      {/* The KPI panel lays out several sibling sections of its own, so this
          wrapper stays a flex column: as a plain block it would collapse the
          margins between them. */}
      <div id="account-snapshot" className="dashboard-section dashboard-section--group"><DashboardKpis /></div>
      <div id="open-positions" className="dashboard-section"><OpenPositionsTable /></div>
      <div id="live-feed" className="dashboard-section"><ScriptStatusIndicator /></div>
      <div id="decision-context" className="dashboard-section"><WhyBotTradedPanel /></div>
      <div id="portfolio-history" className="dashboard-section"><Graph /></div>
      <div id="execution-logs" className="dashboard-section"><OrderCardList /></div>
      <div id="safeguards" className="dashboard-section"><V2SafeguardsPanel /></div>
      {/* Static configuration: collapsed so it never pushes live state off screen. */}
      <div id="risk-management" className="dashboard-section"><RiskControlsPanel /></div>
    </main>
  )
}

export default DashboardHome
