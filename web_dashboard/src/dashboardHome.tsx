import DashboardKpis from './dashboardKpis.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import PageHeader from './pageHeader.tsx'
import RiskControlsPanel from './riskControls.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
import V2SafeguardsPanel from './v2Safeguards.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'

/** The Overview page. A flex column, so the KPI panel's sibling sections keep their margins apart. */
function DashboardHome() {
  return (
    <main className="flex flex-col">
      <PageHeader title="Operations" description="What the bot holds and is doing right now. Paper trading through Alpaca." />
      <DashboardKpis />
      <OpenPositionsTable />
      <ScriptStatusIndicator />
      <WhyBotTradedPanel />
      <Graph />
      <OrderCardList />
      <V2SafeguardsPanel />
      {/* Static configuration: collapsed so it never pushes live state off screen. */}
      <RiskControlsPanel />
    </main>
  )
}

export default DashboardHome
