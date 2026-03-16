import { Bell } from 'lucide-react'
import { useLTPStore } from '@store/ltpStore'
import { useAlertStore } from '@store/alertStore'
import { useAdapter } from '@adapters/AdapterContext'
import { fmtPrice } from '@/lib/utils'
import { useQuote } from '@hooks/useQuote'
import { AlertPanel } from './AlertPanel'

export function Header() {
  const connectionStatus = useLTPStore((s) => s.connectionStatus)
  const wsNifty = useLTPStore((s) => s.ltpMap['NIFTY'])
  const { unreadCount, togglePanel } = useAlertStore()
  const { config } = useAdapter()

  // Quote API is authoritative; fall back to WS store before first fetch
  const { data: niftyQuote } = useQuote('NIFTY', 'NSE_INDEX')
  const niftyLtp = niftyQuote?.ltp ?? wsNifty?.tick.ltp
  const niftyChange = niftyQuote?.change ?? 0
  const niftyIsUp = niftyChange >= 0

  const isLive = connectionStatus === 'CONNECTED'
  const isStale = connectionStatus !== 'CONNECTED' && connectionStatus !== 'CONNECTING'

  return (
    <header className="relative h-12 bg-surface-1 border-b border-border-subtle flex items-center justify-end px-4 gap-3 shrink-0">
      {/* Spot pill */}
      {niftyLtp != null && (
        <div className="flex items-center gap-1.5 px-2.5 py-1 bg-surface-3 border border-border-default rounded-md">
          <span className="text-text-muted text-xs">NIFTY</span>
          <span className="num text-num-sm text-accent-amber font-medium">
            {fmtPrice(niftyLtp)}
          </span>
          {niftyQuote && (
            <span className={`text-[10px] font-mono ${niftyIsUp ? 'text-profit' : 'text-loss'}`}>
              {niftyIsUp ? '+' : ''}{fmtPrice(niftyChange, 2)}
            </span>
          )}
        </div>
      )}

      {/* Connection status */}
      <div className="flex items-center gap-1.5 px-2.5 py-1">
        <div className={
          isLive ? 'status-dot-live' :
          isStale ? 'status-dot-stale' :
          'status-dot-dead'
        } />
        <span className={`text-xs font-medium ${isLive ? 'text-profit' : isStale ? 'text-accent-amber' : 'text-loss'}`}>
          {connectionStatus}
        </span>
      </div>

      {/* Broker pill */}
      <div className="px-2 py-0.5 bg-surface-3 border border-border-subtle rounded text-xs text-text-muted">
        {config.adapter === 'mock' ? 'MockAdapter' : 'OpenAlgo'}
      </div>

      {/* Alert bell */}
      <div className="relative">
        <button
          onClick={togglePanel}
          className="relative p-1.5 text-text-muted hover:text-text-primary transition-colors"
        >
          <Bell size={16} />
          {unreadCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 bg-loss rounded-full text-white text-[10px] font-bold flex items-center justify-center px-0.5">
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          )}
        </button>
        <AlertPanel />
      </div>
    </header>
  )
}
