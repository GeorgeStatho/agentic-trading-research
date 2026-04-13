import './App.css'
import Graph from './Graph.tsx'
import OrderCardList from './orderCard.tsx'
import ScriptStatusIndicator from './scriptStatus.tsx'
function App() {
  return (
    <main className="mainbody">
      <ScriptStatusIndicator />
      <Graph />
      <OrderCardList />
    </main>
  )
}

export default App
