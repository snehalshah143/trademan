import { useEffect, useRef } from 'react'
import { useAlertStore } from '@store/alertStore'
import { toast } from 'react-hot-toast'
import { Bell } from 'lucide-react'

/**
 * AlertToast — mounts once in App.tsx.
 * Watches alertStore for new events and shows react-hot-toast notifications.
 * Also listens for ALERT_SOUND events from the WS (handled in useMarketWebSocket)
 * and plays an audio beep.
 */
export function AlertToast() {
  const events = useAlertStore(s => s.events)
  const prevCountRef = useRef(events.length)

  useEffect(() => {
    const prevCount = prevCountRef.current
    const newCount  = events.length

    if (newCount > prevCount) {
      // New event(s) arrived — show toasts for each new one
      for (let i = prevCount; i < newCount; i++) {
        const ev = events[i]
        toast.custom(
          (t) => (
            <div
              className={`flex items-start gap-3 px-4 py-3 rounded-lg shadow-modal border bg-surface-2 border-accent-amber/40 max-w-sm transition-all ${t.visible ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-2'}`}
              style={{ cursor: 'pointer' }}
              onClick={() => toast.dismiss(t.id)}
            >
              <Bell size={16} className="text-accent-amber shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-text-primary truncate">
                  {ev.type ?? 'Alert Triggered'}
                </p>
                <p className="text-xs text-text-secondary mt-0.5 line-clamp-2">
                  {ev.message}
                </p>
                <p className="text-[10px] text-text-muted mt-1">
                  {new Date(ev.timestamp).toLocaleTimeString('en-IN')}
                </p>
              </div>
            </div>
          ),
          {
            duration: 8000,
            position: 'top-right',
          }
        )
      }
    }

    prevCountRef.current = newCount
  }, [events])

  return null
}
