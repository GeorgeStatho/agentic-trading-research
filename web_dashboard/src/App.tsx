import './App.css'
import DashboardKpis from './dashboardKpis.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import RiskControlsPanel from './riskControls.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'
function App() {
  return (
    <main className="mainbody">
      <DashboardKpis />
      <RiskControlsPanel />
      <ScriptStatusIndicator />
      <WhyBotTradedPanel />
      <Graph />
      <OpenPositionsTable />
      <OrderCardList />
    </main>
  )
}

export default App
