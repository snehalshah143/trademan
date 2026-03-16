import { useState, useMemo } from 'react'
import { ChevronRight, ChevronDown, TrendingUp, Bell, X } from 'lucide-react'
import { fmtINRCompact, profitLossClass, cn } from '@/lib/utils'
import { ExpiryGroup } from './ExpiryGroup'
import { AlertConfigModal } from './AlertConfigModal'
import { ExitConfirmModal } from './ExitConfirmModal'
import type { LiveStrategy } from '@hooks/useLivePositions'
import type { AlertSeverity } from '@/types/domain'
import { useAlertStore } from '@store/alertStore'

interface StrategyRowProps {
  strategy: LiveStrategy
  checkedLegs: Set<string>
  onCheck: (legId: string, checked: boolean) => void
  onSelect: () => void
  isSelected: boolean
}

function alertBorderClass(severity: AlertSeverity | null) {
  if (severity === 'CRITICAL') return 'border-l-2 border-l-loss'
  if (severity === 'WARNING')  return 'border-l-2 border-l-accent-amber'
  return ''
}

export function StrategyRow({ strategy, checkedLegs, onCheck, onSelect, isSelected }: StrategyRowProps) {
  const [expanded, setExpanded] = useState(true)
  const [alertOpen, setAlertOpen] = useState(false)
  const [exitOpen, setExitOpen] = useState(false)

  const events = useAlertStore((s) => s.events)
  const strategyAlerts = useMemo(
    () => events.filter((e) => e.strategyId === strategy.id),
    [events, strategy.id]
  )
  const latestSeverity: AlertSeverity | null = strategyAlerts.length > 0
    ? (strategyAlerts.some((a) => a.severity === 'CRITICAL') ? 'CRITICAL' :
       strategyAlerts.some((a) => a.severity === 'WARNING') ? 'WARNING' : 'INFO')
    : null

  // Group legs by expiry
  const byExpiry = strategy.legs.reduce<Record<string, typeof strategy.legs>>((acc, leg) => {
    const exp = leg.instrument.expiry ?? 'spot'
    if (!acc[exp]) acc[exp] = []
    acc[exp].push(leg)
    return acc
  }, {})

  return (
    <>
      {/* Strategy header row */}
      <tr
        className={cn(
          'cursor-pointer bg-surface-2 border-b border-border-subtle',
          alertBorderClass(latestSeverity),
          isSelected ? 'bg-surface-3' : 'hover:bg-surface-3'
        )}
        onClick={() => { setExpanded((e) => !e); onSelect() }}
      >
        <td className="px-3 py-2.5 text-left w-8">
          {expanded ? <ChevronDown size={14} className="text-text-muted" /> : <ChevronRight size={14} className="text-text-muted" />}
        </td>
        <td className="px-3 py-2.5 text-left" colSpan={5}>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm text-text-primary">{strategy.name}</span>
            <span className="text-text-muted text-xs">#{strategy.id.slice(0, 6)}</span>
            {latestSeverity && (
              <div className={cn('w-1.5 h-1.5 rounded-full', latestSeverity === 'CRITICAL' ? 'bg-loss' : 'bg-accent-amber')} />
            )}
          </div>
        </td>
        <td className="px-3 py-2.5 text-right">
          <span className={cn('num text-num-lg font-bold', profitLossClass(strategy.liveMTM))}>
            {fmtINRCompact(strategy.liveMTM)}
          </span>
        </td>
        <td className="px-3 py-2.5 text-right" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-end gap-1">
            <button
              onClick={() => setAlertOpen(true)}
              className="p-1 text-text-muted hover:text-accent-amber transition-colors"
              title="Alert rules"
            >
              <Bell size={13} />
            </button>
            <button
              onClick={onSelect}
              className="p-1 text-text-muted hover:text-accent-blue transition-colors"
              title="Payoff chart"
            >
              <TrendingUp size={13} />
            </button>
            <button
              onClick={() => setExitOpen(true)}
              className="p-1 text-text-muted hover:text-loss transition-colors"
              title="Exit strategy"
            >
              <X size={13} />
            </button>
          </div>
        </td>
      </tr>

      {/* Expanded legs */}
      {expanded && Object.entries(byExpiry).map(([expiry, legs]) => (
        <ExpiryGroup
          key={expiry}
          expiry={expiry}
          legs={legs}
          checkedLegs={checkedLegs}
          onCheck={onCheck}
          onExitLeg={(_legId) => {/* handled in PositionManager */}}
        />
      ))}

      <AlertConfigModal
        open={alertOpen}
        onOpenChange={setAlertOpen}
        strategy={strategy}
      />
      <ExitConfirmModal
        open={exitOpen}
        onOpenChange={setExitOpen}
        strategy={strategy}
      />
    </>
  )
}
