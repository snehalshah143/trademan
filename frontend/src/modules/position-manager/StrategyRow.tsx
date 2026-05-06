import { useState, useMemo } from 'react'
import { ChevronDown, ChevronRight, Bell, ShieldAlert, X, Trash2 } from 'lucide-react'
import { fmtINRCompact, profitLossClass, cn } from '@/lib/utils'
import { ExpiryGroup } from './ExpiryGroup'
import { AlertConfigModal } from './AlertConfigModal'
import { ExitConfirmModal } from './ExitConfirmModal'
import * as Dialog from '@radix-ui/react-dialog'
import { AlertList } from '@/components/AlertManager/AlertList'
import type { LiveStrategy } from '@hooks/useLivePositions'
import type { AlertSeverity } from '@/types/domain'
import { useAlertStore } from '@store/alertStore'
import { useStrategyStore } from '@store/strategyStore'

interface Props {
  strategy:    LiveStrategy
  checkedLegs: Set<string>
  onCheck:     (legId: string, checked: boolean) => void
  onSelect:    (id: string | null) => void
  isSelected:  boolean
}

export function StrategyRow({ strategy, onSelect, isSelected }: Props) {
  const [expanded,       setExpanded]       = useState(false)
  const [alertOpen,      setAlertOpen]      = useState(false)
  const [exitOpen,       setExitOpen]       = useState(false)
  const [rulesOpen,      setRulesOpen]      = useState(false)
  const [confirmRemove,  setConfirmRemove]  = useState(false)

  const removeStrategy = useStrategyStore((s) => s.removeStrategy)

  const events = useAlertStore((s) => s.events)
  const strategyAlerts = useMemo(
    () => events.filter((e) => e.strategyId === strategy.id),
    [events, strategy.id]
  )
  const latestSeverity: AlertSeverity | null =
    strategyAlerts.length > 0
      ? strategyAlerts.some((a) => a.severity === 'CRITICAL') ? 'CRITICAL'
      : strategyAlerts.some((a) => a.severity === 'WARNING')  ? 'WARNING'
      : 'INFO'
      : null

  // Group legs by expiry
  const byExpiry = strategy.legs.reduce<Record<string, typeof strategy.legs>>((acc, leg) => {
    const exp = leg.instrument.expiry ?? 'spot'
    if (!acc[exp]) acc[exp] = []
    acc[exp].push(leg)
    return acc
  }, {})

  const netPnl = strategy.liveMTM

  return (
    <>
      {/* ── Strategy card header ─────────────────────────────── */}
      <tr
        className={cn(
          'cursor-pointer border-b border-border-default transition-colors',
          latestSeverity === 'CRITICAL' ? 'border-l-2 border-l-loss' :
          latestSeverity === 'WARNING'  ? 'border-l-2 border-l-accent-amber' : '',
          isSelected ? 'bg-surface-3' : 'bg-surface-2 hover:bg-surface-3'
        )}
        onClick={() => {
          if (expanded) {
            setExpanded(false)
            onSelect(null)           // always deselect on collapse
          } else {
            setExpanded(true)
            onSelect(strategy.id)   // select on expand
          }
        }}
      >
        {/* Expand icon + name */}
        <td className="px-4 py-3" colSpan={7}>
          <div className="flex items-center gap-2">
            {expanded
              ? <ChevronDown size={14} className="text-text-muted shrink-0" />
              : <ChevronRight size={14} className="text-text-muted shrink-0" />
            }
            <span className="text-sm font-semibold text-text-primary">{strategy.name}</span>
            <span className="text-text-muted text-xs">#{strategy.id.slice(0, 6)}</span>
            {latestSeverity && (
              <div className={cn(
                'w-1.5 h-1.5 rounded-full',
                latestSeverity === 'CRITICAL' ? 'bg-loss animate-pulse' : 'bg-accent-amber animate-pulse'
              )} />
            )}
          </div>
        </td>

        {/* Net Profit */}
        <td className="px-3 py-3 text-right" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-end gap-3">
            <div className="flex items-center gap-1 text-xs text-text-muted">
              <span>Net Profit</span>
              <span className={cn('num font-bold text-sm', profitLossClass(netPnl))}>
                {netPnl >= 0 ? '+' : ''}{fmtINRCompact(netPnl)}
              </span>
            </div>
            {/* Action buttons */}
            <div className="flex items-center gap-1">
              <button
                onClick={() => setAlertOpen(true)}
                className="p-1 text-text-muted hover:text-accent-amber transition-colors"
                title="Alerts"
              >
                <Bell size={13} />
              </button>
              <button
                onClick={() => setRulesOpen(true)}
                className="p-1 text-text-muted hover:text-accent-purple transition-colors"
                title="Alert rules"
              >
                <ShieldAlert size={13} />
              </button>
              <button
                onClick={() => setExitOpen(true)}
                className="p-1 text-text-muted hover:text-loss transition-colors"
                title="Exit strategy"
              >
                <X size={13} />
              </button>
              {/* Remove from view */}
              {!confirmRemove ? (
                <button
                  onClick={() => setConfirmRemove(true)}
                  className="p-1 text-text-muted hover:text-loss transition-colors"
                  title="Remove from strategies view"
                >
                  <Trash2 size={13} />
                </button>
              ) : (
                <div className="flex items-center gap-1 ml-1">
                  <span className="text-[10px] text-text-muted">Remove?</span>
                  <button
                    onClick={() => removeStrategy(strategy.id)}
                    className="px-2 py-0.5 text-[10px] font-medium bg-loss text-white rounded transition-colors hover:bg-red-600"
                  >
                    Yes
                  </button>
                  <button
                    onClick={() => setConfirmRemove(false)}
                    className="px-2 py-0.5 text-[10px] font-medium border border-border-default text-text-secondary rounded hover:text-text-primary transition-colors"
                  >
                    No
                  </button>
                </div>
              )}
            </div>
          </div>
        </td>

        {/* Placeholder exit column */}
        <td />
      </tr>

      {/* ── Column headers (once per strategy) ──────────────── */}
      {expanded && (
        <tr className="bg-surface-2 border-b border-border-subtle text-[10px] text-text-muted uppercase tracking-wider font-medium">
          <th className="px-4 py-1.5 text-left w-24">Action</th>
          <th className="px-3 py-1.5 text-left">Trade Instrument</th>
          <th className="px-3 py-1.5 text-center w-20">Product</th>
          <th className="px-3 py-1.5 text-center w-16">Lots</th>
          <th className="px-3 py-1.5 text-center w-24">Qty</th>
          <th className="px-3 py-1.5 text-right w-20">Avg Price</th>
          <th className="px-3 py-1.5 text-right w-20">LTP</th>
          <th className="px-3 py-1.5 text-right w-28">P & L</th>
          <th className="px-3 py-1.5 text-center w-12">Exit</th>
        </tr>
      )}

      {/* ── Expiry groups (legs only, no sub-header) ─────────── */}
      {expanded && Object.entries(byExpiry).map(([expiry, legs]) => (
        <ExpiryGroup
          key={expiry}
          expiry={expiry}
          legs={legs}
          onExitLeg={() => {/* handled in PositionManager */}}
        />
      ))}

      {/* Spacer row between strategies */}
      <tr className="h-2 bg-transparent" />

      <AlertConfigModal open={alertOpen} onOpenChange={setAlertOpen} strategy={strategy} />
      <ExitConfirmModal open={exitOpen} onOpenChange={setExitOpen} strategy={strategy} />

      <Dialog.Root open={rulesOpen} onOpenChange={setRulesOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[560px] max-w-[95vw] h-[580px] bg-surface-1 border border-border-default rounded-xl shadow-modal overflow-hidden flex flex-col">
            <Dialog.Title className="sr-only">Alert Rules</Dialog.Title>
            <AlertList
              strategyId={strategy.id}
              strategyName={strategy.name}
              positionLegs={strategy.legs.map((l) => ({
                leg_id: l.id,
                symbol: l.instrument.symbol,
                side:   l.side,
              }))}
            />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  )
}
