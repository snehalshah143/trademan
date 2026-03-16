import { useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { useLTPStore } from '@store/ltpStore'

export function StaleBanner() {
  const [dismissed, setDismissed] = useState(false)
  const isAnyStale = useLTPStore((s) => s.isAnyStale())
  const connectionStatus = useLTPStore((s) => s.connectionStatus)

  // Auto-show again when stale
  const shouldShow = (isAnyStale || connectionStatus === 'DISCONNECTED') && !dismissed

  if (!shouldShow) return null

  const message = connectionStatus === 'DISCONNECTED'
    ? 'Disconnected from market data server'
    : 'Market data is stale — prices may be outdated'

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-amber-500/10 border-b border-amber-500/30 text-accent-amber text-sm">
      <AlertTriangle size={14} className="shrink-0" />
      <span className="flex-1">{message}</span>
      <button
        onClick={() => setDismissed(true)}
        className="text-accent-amber/60 hover:text-accent-amber ml-2"
      >
        <X size={14} />
      </button>
    </div>
  )
}
