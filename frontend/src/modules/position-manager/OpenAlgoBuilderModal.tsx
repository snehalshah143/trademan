import { useEffect, useRef, useState } from 'react'
import { ExternalLink, Layers, RefreshCw, X, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

const OA_BUILDER_URL = 'http://127.0.0.1:5000/strategybuilder'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called automatically when the builder window closes — triggers import in parent */
  onDone: () => void
}

export function OpenAlgoBuilderModal({ open, onOpenChange, onDone }: Props) {
  const popupRef    = useRef<Window | null>(null)
  const timerRef    = useRef<ReturnType<typeof setInterval> | null>(null)
  const doneFiredRef = useRef(false)
  const [popupOpen, setPopupOpen] = useState(false)
  const [syncing,   setSyncing]   = useState(false)

  // Reset state and open popup whenever this modal opens
  useEffect(() => {
    if (!open) return
    doneFiredRef.current = false
    setSyncing(false)

    const popup = window.open(
      OA_BUILDER_URL,
      'oa-strategy-builder',
      'width=1280,height=800,left=100,top=80,resizable=yes,scrollbars=yes'
    )
    popupRef.current = popup
    setPopupOpen(!!popup)

    // Poll every second — auto-sync the moment the builder window is closed
    timerRef.current = setInterval(() => {
      if (popupRef.current?.closed) {
        clearInterval(timerRef.current!)
        setPopupOpen(false)
        if (!doneFiredRef.current) {
          doneFiredRef.current = true
          setSyncing(true)
          onDone()
        }
      }
    }, 1000)

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  function handleFocusPopup() {
    popupRef.current?.focus()
  }

  function handleManualDone() {
    if (!doneFiredRef.current) {
      doneFiredRef.current = true
      setSyncing(true)
      onDone()
    }
    popupRef.current?.close()
  }

  function handleClose() {
    if (timerRef.current) clearInterval(timerRef.current)
    popupRef.current?.close()
    onOpenChange(false)
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-surface-1 border border-border-subtle rounded-xl shadow-2xl w-[460px] flex flex-col overflow-hidden">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
          <div className="flex items-center gap-2">
            <Layers size={16} className="text-accent-blue" />
            <span className="text-sm font-semibold text-text-primary">OpenAlgo Strategy Builder</span>
          </div>
          <button
            onClick={handleClose}
            className="p-1.5 text-text-muted hover:text-text-primary hover:bg-surface-3 rounded transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 flex flex-col items-center gap-4 text-center">

          {/* Status pill */}
          <div className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium',
            syncing
              ? 'bg-accent-blue/10 text-accent-blue'
              : popupOpen
                ? 'bg-profit/10 text-profit'
                : 'bg-accent-amber/10 text-accent-amber'
          )}>
            <span className={cn(
              'w-2 h-2 rounded-full',
              syncing ? 'bg-accent-blue animate-pulse'
                : popupOpen ? 'bg-profit animate-pulse'
                : 'bg-accent-amber'
            )} />
            {syncing
              ? 'Syncing strategies to Trademan…'
              : popupOpen
                ? 'Strategy Builder is open — build your strategy there'
                : 'Builder window closed — syncing…'}
          </div>

          <p className="text-xs text-text-muted leading-relaxed">
            Build your strategy in the OpenAlgo window.<br />
            <strong className="text-text-primary">Close that window</strong> when done —
            Trademan will automatically import it.
          </p>

          {/* Steps */}
          <div className="w-full bg-surface-2 rounded-lg px-4 py-3 text-left flex flex-col gap-2">
            {[
              'Select underlying, expiry, and strikes in the builder',
              'Add legs using the template or manually',
              'Click "Add to Simulation" or "Add to Trade"',
              'Close the builder window — Trademan auto-imports',
            ].map((step, i) => (
              <div key={i} className="flex items-start gap-2.5">
                <span className="shrink-0 w-5 h-5 rounded-full bg-accent-blue/15 text-accent-blue text-[10px] font-bold flex items-center justify-center mt-px">
                  {i + 1}
                </span>
                <span className="text-xs text-text-secondary">{step}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 px-5 py-4 border-t border-border-subtle bg-surface-2/40">
          {syncing ? (
            <div className="flex items-center gap-2 text-xs text-text-muted">
              <Loader2 size={12} className="animate-spin" />
              Importing strategies…
            </div>
          ) : popupOpen ? (
            <button
              onClick={handleFocusPopup}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-text-muted hover:text-text-primary border border-border-default rounded-md transition-colors"
            >
              <ExternalLink size={12} />
              Focus Builder Window
            </button>
          ) : (
            <button
              onClick={() => {
                const popup = window.open(
                  OA_BUILDER_URL,
                  'oa-strategy-builder',
                  'width=1280,height=800,left=100,top=80,resizable=yes,scrollbars=yes'
                )
                popupRef.current = popup
                doneFiredRef.current = false
                setSyncing(false)
                setPopupOpen(!!popup)
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-text-muted hover:text-text-primary border border-border-default rounded-md transition-colors"
            >
              <RefreshCw size={12} />
              Reopen Builder
            </button>
          )}

          {!syncing && (
            <button
              onClick={handleManualDone}
              className="ml-auto px-4 py-1.5 text-xs font-semibold bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
            >
              Done — Sync Now
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
