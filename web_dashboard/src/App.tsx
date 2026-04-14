import './App.css'
import DashboardKpis from './dashboardKpis.tsx'
import Graph from './Graph.tsx'
import OpenPositionsTable from './openPositionsTable.tsx'
import OrderCardList from './orderCard.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
function App() {
  return (
    <main className="mainbody">
      <DashboardKpis />
      <ScriptStatusIndicator />
      <Graph />
      <OpenPositionsTable />
      <OrderCardList />
    </main>
  )
}

export default App
