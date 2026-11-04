import DashboardKpis from './dashboardKpis.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import PageHeader from './pageHeader.tsx'
import V2SafeguardsPanel from './v2Safeguards.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'

/** The Overview page. */
function DashboardHome() {
  return (
    <main className="flex flex-col gap-3">
      <PageHeader title="Operations" description="What the bot holds and is doing right now. Paper trading through Alpaca." />
      <DashboardKpis />
      <OpenPositionsTable />
      <WhyBotTradedPanel />
      <Graph />
      <OrderCardList />
      <V2SafeguardsPanel />
    </main>
  )
}

export default DashboardHome
