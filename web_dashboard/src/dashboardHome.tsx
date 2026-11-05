import DashboardKpis from './dashboardKpis.tsx'
import Graph from './Graph.tsx'
import LiveDecisions from './liveDecisions.tsx'
import LivePipeline from './livePipeline.tsx'
import LiveSafeguards from './liveSafeguards.tsx'
import LiveNewsLag from './liveNewsLag.tsx'
import LiveNewsfeed from './liveNewsfeed.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import PageHeader from './pageHeader.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'

/** The Overview page. */
function DashboardHome() {
  return (
    <main className="flex flex-col gap-3">
      <PageHeader title="Operations" description="What the bot holds and is doing right now. Paper trading through Alpaca." />
      <DashboardKpis />
      <WhyBotTradedPanel />
      <Graph />
      <div className="flex flex-wrap gap-3">
        <div className="min-w-0 flex-[2_1_640px]"><LiveNewsfeed /></div>
        <div className="min-w-0 flex-[1_1_360px]"><LiveDecisions /></div>
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="min-w-0 flex-[2_1_640px]"><OrderCardList /></div>
        <div className="min-w-0 flex-[1_1_360px]"><LiveNewsLag /></div>
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="min-w-0 flex-[1_1_360px]"><OpenPositionsTable /></div>
        <div className="min-w-0 flex-[1_1_360px]"><LivePipeline /></div>
        <div className="min-w-0 flex-[1_1_360px]"><LiveSafeguards /></div>
      </div>
    </main>
  )
}

export default DashboardHome
