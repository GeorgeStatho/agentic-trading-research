import './App.css'
import DashboardKpis from './dashboardKpis.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
import WhyBotTradedPanel from './whyBotTraded.tsx'
function App() {
  return (
    <main className="mainbody">
      <DashboardKpis />
      <ScriptStatusIndicator />
      <WhyBotTradedPanel />
      <Graph />
      <OpenPositionsTable />
      <OrderCardList />
    </main>
  )
}

export default App
