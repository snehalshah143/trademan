import { Routes, Route } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { PositionManager } from './modules/position-manager/PositionManager'
import { StrategyBuilder } from './modules/strategy-builder/StrategyBuilder'
import { Settings } from './modules/settings/Settings'
import { AlertManager } from './pages/AlertManager'
import { MonitoredPositions } from './pages/MonitoredPositions'
import { useMarketWebSocket } from './hooks/useMarketWebSocket'
import { AlertToast } from './components/AlertManager/AlertToast'

function OrderBookPlaceholder() {
  return (
    <div className="flex items-center justify-center h-full py-24 text-text-muted text-sm">
      Order Book — coming soon
    </div>
  )
}

export function App() {
  useMarketWebSocket()

  return (
    <AppShell>
      <Routes>
        <Route path="/"         element={<PositionManager />} />
        <Route path="/builder"  element={<StrategyBuilder />} />
        <Route path="/monitors"  element={<MonitoredPositions />} />
        <Route path="/alerts"   element={<AlertManager />} />
        <Route path="/orders"   element={<OrderBookPlaceholder />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
      <AlertToast />
    </AppShell>
  )
}
