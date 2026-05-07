import { useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { AppShell } from './components/layout/AppShell'
import { PositionManager } from './modules/position-manager/PositionManager'
import { StrategyBuilder } from './modules/strategy-builder/StrategyBuilder'
import { Settings } from './modules/settings/Settings'
import { AlertManager } from './pages/AlertManager'
import { MonitoredPositions } from './pages/MonitoredPositions'
import { useMarketWebSocket } from './hooks/useMarketWebSocket'
import { AlertToast } from './components/AlertManager/AlertToast'
import { positionService } from './services/positionService'

function OrderBookPlaceholder() {
  return (
    <div className="flex items-center justify-center h-full py-24 text-text-muted text-sm">
      Order Book — coming soon
    </div>
  )
}

const PREFETCH_EXCHANGES = ['NFO', 'NSE', 'BSE', 'BFO', 'MCX']

export function App() {
  useMarketWebSocket()
  const queryClient = useQueryClient()

  // Pre-warm symbol cache for all exchanges on startup
  useEffect(() => {
    for (const exchange of PREFETCH_EXCHANGES) {
      queryClient.prefetchQuery({
        queryKey: ['symbol-list', exchange],
        queryFn: () => positionService.listSymbols(exchange),
        staleTime: 5 * 60 * 1000, // 5 minutes
      })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

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
