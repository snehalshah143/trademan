import { X, Bell } from 'lucide-react'
import { useAlertStore } from '@store/alertStore'
import { fmtRelative } from '@/lib/utils'
import type { AlertSeverity } from '@/types/domain'

function severityDot(severity: AlertSeverity) {
  if (severity === 'CRITICAL') return 'bg-loss'
  if (severity === 'WARNING')  return 'bg-accent-amber'
  return 'bg-accent-blue'
}

export function AlertPanel() {
  const { isPanelOpen, events, acknowledgeEvent, acknowledgeAll, closePanel } = useAlertStore()

  if (!isPanelOpen) return null

  return (
    <div className="absolute top-12 right-0 z-40 w-96 bg-surface-1 border border-border-default rounded-b-lg shadow-modal animate-fade-in">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle">
        <span className="text-sm font-medium text-text-primary">Alerts</span>
        <div className="flex items-center gap-2">
          {events.length > 0 && (
            <button
              onClick={acknowledgeAll}
              className="text-xs text-text-muted hover:text-text-secondary transition-colors"
            >
              Dismiss all
            </button>
          )}
          <button onClick={closePanel} className="text-text-muted hover:text-text-primary">
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="max-h-64 overflow-y-auto custom-scroll">
        {events.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-text-muted">
            <Bell size={20} className="mb-2 opacity-40" />
            <span className="text-xs">No active alerts</span>
          </div>
        ) : (
          events.map((evt) => (
            <div
              key={evt.id}
              className={`flex items-start gap-3 px-4 py-3 border-b border-border-subtle last:border-0 ${evt.acknowledged ? 'opacity-50' : ''}`}
            >
              <div className={`w-2 h-2 rounded-full mt-1 shrink-0 ${severityDot(evt.severity)}`} />
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-text-primary truncate">{evt.strategyName}</div>
                <div className="text-xs text-text-secondary mt-0.5">{evt.message}</div>
                <div className="text-xs text-text-muted mt-0.5">{fmtRelative(evt.timestamp)}</div>
              </div>
              {!evt.acknowledged && (
                <button
                  onClick={() => acknowledgeEvent(evt.id)}
                  className="text-text-muted hover:text-text-primary shrink-0"
                >
                  <X size={12} />
                </button>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
